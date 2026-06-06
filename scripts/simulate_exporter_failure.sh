#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

set +e
output="$(
  KASPA_WATCHTOWER_EXPORTER_URL="http://127.0.0.1:1" \
    scripts/check_integrations.sh 2>&1
)"
status=$?
set -e

if [ "$status" -eq 0 ]; then
  printf 'FAIL exporter failure simulation expected check_integrations to fail\n' >&2
  printf '%s\n' "$output" >&2
  exit 1
fi

if ! grep -Fq 'FAIL exporter health endpoint unavailable' <<<"$output"; then
  printf 'FAIL exporter failure simulation did not report exporter health failure\n' >&2
  printf '%s\n' "$output" >&2
  exit 1
fi

printf 'OK exporter failure simulation\n'
