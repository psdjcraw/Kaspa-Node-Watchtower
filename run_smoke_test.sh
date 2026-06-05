#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p state

OUTPUT_FILE="state/last-smoke-test.txt"
if scripts/smoke_test.sh >"$OUTPUT_FILE" 2>&1; then
  exit 0
fi

cat "$OUTPUT_FILE"
exit 1
