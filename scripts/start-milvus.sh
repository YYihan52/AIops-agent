#!/usr/bin/env bash
# Bring up just the Milvus vector store (etcd + minio + milvus-standalone) without
# the whole OTel demo stack. Used by the agent's incident memory / RAG (agent/memory.py).
#
# Milvus is ready when its health endpoint on :9091 returns OK and gRPC :19530 accepts
# connections. First start can take ~1-2 min (image pull + milvus bootstrap).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[milvus] starting etcd + minio + milvus-standalone …"
docker-compose up -d milvus-etcd milvus-minio milvus-standalone

echo "[milvus] waiting for Milvus health (:9091/healthz) …"
for _ in $(seq 1 60); do
  if curl -sf http://localhost:9091/healthz >/dev/null 2>&1; then
    echo "[milvus] healthy. gRPC endpoint: localhost:19530"
    exit 0
  fi
  sleep 3
done

echo "[milvus] WARNING: health check did not pass within timeout."
echo "[milvus] check: docker logs aiops-milvus --tail 50"
exit 1
