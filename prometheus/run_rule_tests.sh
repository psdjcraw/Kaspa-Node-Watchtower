#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
docker run --rm \
  --entrypoint promtool \
  -v "$PWD:/rules:ro" \
  prom/prometheus:v2.55.1 \
  test rules /rules/kaspa-watchtower-rules.test.yml
