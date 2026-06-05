#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

mkdir -p state
OUTPUT_FILE="state/last-benchmark-snapshot.txt"
if "$PYTHON_BIN" watchtower.py -c config.json --benchmark-snapshot >"$OUTPUT_FILE" 2>&1; then
  exit 0
fi

cat "$OUTPUT_FILE"
exit 1
