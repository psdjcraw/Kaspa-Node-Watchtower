#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p state

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

OUTPUT_FILE="state/last-smoke-test.txt"
if scripts/smoke_test.sh >"$OUTPUT_FILE" 2>&1; then
  exit 0
fi

cat "$OUTPUT_FILE"
exit 1
