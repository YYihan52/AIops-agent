#!/usr/bin/env bash
# 起一个本地 kind 集群，作为 k8s 后端（AIOPS_BACKEND=k8s）的运行环境。
#
# 这是「体验生产形态」的可选路径——默认 docker 后端不需要它。跑完这个脚本后：
#   export AIOPS_BACKEND=k8s
# 诊断 Agent 的低风险白名单就会切成 kubectl rollout restart / delete pod / set resources，
# 观测命令切成 kubectl get/describe/top pod。
#
# 装了什么：
#   - 一个单节点 kind 集群
#   - metrics-server（让 `kubectl top pod` 有数据；kind 上需加 --kubelet-insecure-tls）
#   - otel-demo 命名空间
#
# 前置：已装 docker/colima + kind + kubectl。
#   kind:    https://kind.sigs.k8s.io/docs/user/quick-start/#installation
#   kubectl: https://kubernetes.io/docs/tasks/tools/
set -euo pipefail

CLUSTER="${KIND_CLUSTER:-aiops}"
NS="${AIOPS_K8S_NAMESPACE:-otel-demo}"

command -v kind >/dev/null    || { echo "[kind] kind 未安装：https://kind.sigs.k8s.io/"; exit 1; }
command -v kubectl >/dev/null || { echo "[kind] kubectl 未安装：https://kubernetes.io/docs/tasks/tools/"; exit 1; }

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "[kind] 集群 '$CLUSTER' 已存在，复用。"
else
  echo "[kind] 创建集群 '$CLUSTER'…"
  kind create cluster --name "$CLUSTER"
fi

echo "[kind] 安装 metrics-server（kubectl top 需要）…"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# kind 节点的 kubelet 证书是自签的，metrics-server 默认会拒连，加这个 flag 放开。
kubectl -n kube-system patch deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' \
  >/dev/null 2>&1 || echo "[kind] （metrics-server patch 已存在或失败，可在 Settings 手动加 --kubelet-insecure-tls）"

echo "[kind] 创建命名空间 '$NS'…"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

echo ""
echo "[kind] DONE。切到 k8s 后端："
echo "    export AIOPS_BACKEND=k8s"
echo "    export AIOPS_K8S_NAMESPACE=$NS"
echo "  然后注入 s4 内存泄漏 capstone："
echo "    ./scripts/inject.sh s4        # 会 kind load 泄漏镜像并 kubectl apply Deployment"
echo "  拆集群：kind delete cluster --name $CLUSTER"
