#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

INTERVAL_SECONDS="${BPS_HIGHWAY_INTERVAL_SECONDS:-5}"
while true; do
  "$PYTHON_BIN" watchtower.py -c config.json --bps-highway-snapshot
  sleep "$INTERVAL_SECONDS"
done
