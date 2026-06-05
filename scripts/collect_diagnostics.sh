#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

OUTPUT_DIR="${KASPA_WATCHTOWER_DIAGNOSTICS_DIR:-state/diagnostics}"
mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/diagnostics-$(date '+%Y%m%d-%H%M%S').txt"

section() {
  printf '\n== %s ==\n' "$1"
}

run_section() {
  local title="$1"
  shift
  section "$title"
  "$@" 2>&1
  local status=$?
  if [ "$status" -ne 0 ]; then
    printf 'command_failed exit=%s command=%q' "$status" "$1"
    shift || true
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
  fi
}

{
  section "Diagnostic Metadata"
  date '+generated_at=%Y-%m-%dT%H:%M:%S%z'
  printf 'repo=%s\n' "$(pwd)"
  printf 'host=%s\n' "$(hostname)"
  printf 'user=%s\n' "$(id -un)"

  run_section "Git Revision" git log -1 --oneline
  run_section "Git Status" git status --short --branch
  run_section "Config Validation" "$PYTHON_BIN" watchtower.py -c config.json --validate-config
  run_section "Watchtower Summary" "$PYTHON_BIN" watchtower.py -c config.json --summary
  run_section "Benchmark Report" "$PYTHON_BIN" watchtower.py -c config.json --benchmark-report
  run_section "Operations Snapshot" scripts/ops_snapshot.sh
  run_section "Integration Checks" scripts/check_integrations.sh
  run_section "GitHub Actions" scripts/check_ci_status.sh
  run_section "Prometheus Metrics Sample" sh -c "curl -fsS http://127.0.0.1:9660/metrics | sed -n '1,80p'"
  run_section "State Files" ls -lh state/watchtower-state.json state/watchtower.prom state/status.html state/benchmarks.jsonl
  run_section "Recent Smoke Output" tail -n 80 state/last-smoke-test.txt
  run_section "Recent Benchmark Output" tail -n 80 state/last-benchmark-snapshot.txt
  run_section "Exporter Stdout" tail -n 80 state/prometheus-exporter.out.log
  run_section "Exporter Stderr" tail -n 80 state/prometheus-exporter.err.log
} >"$OUTPUT_FILE"

printf 'Diagnostics written: %s\n' "$OUTPUT_FILE"
