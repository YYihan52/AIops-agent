#!/usr/bin/env bash
# Inject a failure scenario.
#   s1 -> flagd productCatalogFailure
#   s2 -> flagd adServiceHighCpu
#   s3 -> flagd kafkaQueueProblems
#   s4 -> deploy the planted memory-leak recommendation image (seeded by seed-github.sh)
#
# For s1-s3 we flip the flag in the OTel demo's flagd config. For s4 we deploy the
# planted-leak recommendation image — via kubectl on a kind cluster when
# AIOPS_BACKEND=k8s (production form), or via `docker run` otherwise (local default).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO="${1:-}"
FLAGD_CONFIG="${FLAGD_CONFIG:-$ROOT/vendor/otel-demo/src/flagd/demo.flagd.json}"
BACKEND="${AIOPS_BACKEND:-docker}"
K8S_NS="${AIOPS_K8S_NAMESPACE:-otel-demo}"
KIND_CLUSTER="${KIND_CLUSTER:-aiops}"

flip_flag() {
  local flag="$1"
  if [ ! -f "$FLAGD_CONFIG" ]; then
    echo "[inject] flagd config not found at $FLAGD_CONFIG"
    echo "[inject] run ./scripts/fetch-otel-demo.sh first, or set FLAGD_CONFIG."
    echo "[inject] MANUAL: set flag '$flag' defaultVariant to 'on' and restart flagd."
    return 0
  fi
  # flagd hot-reloads its config file; set defaultVariant to "on".
  python3 - "$FLAGD_CONFIG" "$flag" <<'PY'
import json, sys
path, flag = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
flags = cfg.get("flags", {})
if flag in flags:
    flags[flag]["defaultVariant"] = "on"
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"[inject] set {flag}.defaultVariant=on in {path}")
else:
    print(f"[inject] flag {flag} not present in config; available: {list(flags)[:10]}")
PY
}

case "$SCENARIO" in
  s1) flip_flag "productCatalogFailure" ;;
  s2) flip_flag "adHighCpu" ;;
  s3) flip_flag "kafkaQueueProblems" ;;
  s4)
    echo "[inject] s4: building + deploying the planted-leak recommendation image (backend=$BACKEND)."
    BAD_SHA=$(grep 'suspect' "$ROOT/deploys.log" 2>/dev/null | sed -n 's/.*sha=\([^ ]*\).*/\1/p' | head -1 || true)
    if [ -z "${BAD_SHA:-}" ]; then
      echo "[inject] run ./scripts/seed-github.sh first (it writes deploys.log + the leak commit)."
      exit 1
    fi
    FIXTURE="$ROOT/scripts/fixtures/recommendation"
    IMAGE="recommendation:$BAD_SHA"

    echo "[inject] building $IMAGE from leak commit ($BAD_SHA) — pure-stdlib image, fast build…"
    docker build -q -t "$IMAGE" -t recommendation:latest "$FIXTURE" >/dev/null

    if [ "$BACKEND" = "k8s" ]; then
      # --- k8s path (production form): kind load image -> apply Deployment with a tight memory limit ---
      command -v kubectl >/dev/null || { echo "[inject] kubectl 未安装；先跑 scripts/k8s/start-kind.sh"; exit 1; }
      echo "[inject] loading $IMAGE into kind cluster '$KIND_CLUSTER'…"
      kind load docker-image "$IMAGE" --name "$KIND_CLUSTER" >/dev/null 2>&1 \
        || echo "[inject] （kind load 失败：确认集群已起 ./scripts/k8s/start-kind.sh）"
      kubectl create namespace "$K8S_NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
      echo "[inject] applying Deployment (memory limit 128Mi so the leak OOM-kills the Pod)…"
      sed "s|__IMAGE__|$IMAGE|g" "$ROOT/scripts/k8s/recommendation-leak.yaml" | kubectl apply -f -
      # 强制拉起新 Pod 用上新镜像（同名镜像不会自动重建）
      kubectl -n "$K8S_NS" rollout restart deploy/recommendation >/dev/null 2>&1 || true
      echo "[inject] recommendation deployed to ns=$K8S_NS image=$IMAGE"
      echo "[inject] observe the climb:   kubectl top pod -n $K8S_NS -l app=recommendation"
      echo "[inject]                       kubectl describe pod -n $K8S_NS -l app=recommendation | grep -A3 'Last State'"
      echo "[inject] it will OOMKill + restart within ~1-2 min; RESTARTS climbs in: kubectl get pod -n $K8S_NS -l app=recommendation"
    else
      # --- docker path (local default): docker run with --memory cap ---
      CNAME="${RECO_CONTAINER:-recommendation}"
      MEM_LIMIT="${RECO_MEM_LIMIT:-128m}"
      HOST_PORT="${RECO_PORT:-8080}"

      echo "[inject] (re)starting container '$CNAME' with --memory=$MEM_LIMIT so the leak OOM-kills it…"
      docker rm -f "$CNAME" >/dev/null 2>&1 || true
      # --memory caps the working set; the self-driving load thread climbs RSS until OOMKilled,
      # so restart count rises -> a real, observable runtime fault for 故障诊断处置 Agent (docker stats / inspect).
      docker run -d --name "$CNAME" \
        --memory="$MEM_LIMIT" --memory-swap="$MEM_LIMIT" \
        --restart=on-failure \
        -p "$HOST_PORT:8080" \
        -e LOAD_RPS="${RECO_LOAD_RPS:-400}" \
        "$IMAGE" >/dev/null

      echo "[inject] recommendation running: image=$IMAGE container=$CNAME mem=$MEM_LIMIT port=$HOST_PORT"
      echo "[inject] observe the climb:   docker stats --no-stream $CNAME"
      echo "[inject]                       curl -s localhost:$HOST_PORT/metrics"
      echo "[inject] it will OOMKill + restart within ~1-2 min; restart count climbs in: docker inspect $CNAME --format '{{.RestartCount}}'"
    fi
    ;;
  *)
    echo "usage: $0 {s1|s2|s3|s4}"; exit 1 ;;
esac

echo "[inject] scenario '$SCENARIO' injected. Give it ~2-3 min under load, then run the agent:"
echo "         python -m agent.run --alert alerts/$SCENARIO.json"
