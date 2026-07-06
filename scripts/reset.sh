#!/usr/bin/env bash
# Reset all injected scenarios back to baseline:
#   - flip every demo flagd flag back to "off"
#   - point recommendation back at the clean image (manual note)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLAGD_CONFIG="${FLAGD_CONFIG:-$ROOT/vendor/otel-demo/src/flagd/demo.flagd.json}"

if [ -f "$FLAGD_CONFIG" ]; then
  python3 - "$FLAGD_CONFIG" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
for name, flag in cfg.get("flags", {}).items():
    if "off" in flag.get("variants", {}):
        flag["defaultVariant"] = "off"
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print(f"[reset] all flags set defaultVariant=off in {path}")
PY
else
  echo "[reset] flagd config not found ($FLAGD_CONFIG); nothing to reset for s1-s3."
fi

BACKEND="${AIOPS_BACKEND:-docker}"
K8S_NS="${AIOPS_K8S_NAMESPACE:-otel-demo}"
if [ "$BACKEND" = "k8s" ]; then
  if command -v kubectl >/dev/null; then
    echo "[reset] s4 (k8s): deleting recommendation Deployment/Service in ns=$K8S_NS…"
    kubectl -n "$K8S_NS" delete deploy/recommendation svc/recommendation >/dev/null 2>&1 || true
  fi
else
  CNAME="${RECO_CONTAINER:-recommendation}"
  if docker ps -a --format '{{.Names}}' | grep -qx "$CNAME"; then
    echo "[reset] s4: removing leaking container '$CNAME'…"
    docker rm -f "$CNAME" >/dev/null 2>&1 || true
  fi
fi
CLEAN_SHA=$(grep 'baseline' "$ROOT/deploys.log" 2>/dev/null | sed -n 's/.*sha=\([^ ]*\).*/\1/p' | head -1 || true)
if [ -n "${CLEAN_SHA:-}" ]; then
  echo "[reset] s4: baseline image is recommendation:$CLEAN_SHA (bounded; rebuild from baseline commit if you want it running)."
fi

echo "[reset] done."
