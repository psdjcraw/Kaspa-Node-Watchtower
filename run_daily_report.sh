#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

section() {
  printf '\n== %s ==\n' "$1"
}

section "Kaspa Watchtower Daily Report"
date '+generated_at=%Y-%m-%dT%H:%M:%S%z'

section "Node Summary"
"$PYTHON_BIN" watchtower.py -c config.json --summary

section "Benchmark Trend"
"$PYTHON_BIN" watchtower.py -c config.json --benchmark-report

section "Integrations"
scripts/check_integrations.sh

section "GitHub Actions"
scripts/check_ci_status.sh

section "Dashboard"
printf 'status_html=%s\n' "state/status.html"
printf 'canvas_html=%s\n' "/Users/psdjc/.openclaw/canvas/kaspa-watchtower/status.html"
printf 'grafana=%s\n' "http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower"
