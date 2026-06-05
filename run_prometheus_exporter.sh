#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

export KASPA_WATCHTOWER_METRICS_PATH="${KASPA_WATCHTOWER_METRICS_PATH:-$PWD/state/watchtower.prom}"
export KASPA_WATCHTOWER_EXPORTER_HOST="${KASPA_WATCHTOWER_EXPORTER_HOST:-127.0.0.1}"
export KASPA_WATCHTOWER_EXPORTER_PORT="${KASPA_WATCHTOWER_EXPORTER_PORT:-9660}"

exec "$PYTHON_BIN" prometheus_file_server.py
