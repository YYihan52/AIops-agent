#!/usr/bin/env bash
# Fetch the upstream OpenTelemetry Demo docker-compose into ./vendor/otel-demo
# so our top-level docker-compose.yml can `include` it.
#
#   ./scripts/fetch-otel-demo.sh           # full demo (all services + load gen + flagd)
#
# Pinned to a known-good release tag for reproducibility.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/otel-demo"
TAG="${OTEL_DEMO_TAG:-2.0.2}"

mkdir -p "$VENDOR"
cd "$VENDOR"

if [ ! -d .git ]; then
  echo "[fetch] cloning opentelemetry-demo @ $TAG (shallow)…"
  git clone --depth 1 --branch "$TAG" https://github.com/open-telemetry/opentelemetry-demo.git .
else
  echo "[fetch] already present at $VENDOR (tag pinned $TAG)"
fi

echo "[fetch] done. Top-level docker-compose.yml includes vendor/otel-demo/docker-compose.yml"
echo "[fetch] NOTE: the full demo is heavy (~10 services). Ensure Docker has >=6GB RAM."
