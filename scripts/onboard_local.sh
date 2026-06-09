#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG="${CONFIG:-config.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
DO_BOOTSTRAP=0
DO_WRITE_CONFIG=0
DO_SMOKE=0

usage() {
  cat <<'EOF'
Usage: scripts/onboard_local.sh [--write-config] [--bootstrap] [--smoke]

Guided local onboarding for Kaspa Node Watchtower.

Default mode is check-only:
  - shows repo, Python, venv, config, state, and launchd hints
  - validates config when config.json exists
  - prints next commands

Options:
  --write-config  Copy config.example.json to config.json when missing
  --bootstrap     Run scripts/bootstrap_env.sh
  --smoke         Run scripts/smoke_test.sh after checks
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --write-config)
      DO_WRITE_CONFIG=1
      shift
      ;;
    --bootstrap)
      DO_BOOTSTRAP=1
      shift
      ;;
    --smoke)
      DO_SMOKE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

section() {
  printf '\n== %s ==\n' "$1"
}

run_optional() {
  local label="$1"
  shift
  printf '%s: ' "$label"
  if "$@" >/tmp/kaspa-watchtower-onboard.out 2>/tmp/kaspa-watchtower-onboard.err; then
    printf 'ok\n'
  else
    printf 'check failed\n'
    sed -n '1,20p' /tmp/kaspa-watchtower-onboard.err
    sed -n '1,20p' /tmp/kaspa-watchtower-onboard.out
  fi
}

if [ -x "$VENV_DIR/bin/python" ]; then
  RUNTIME_PYTHON="$VENV_DIR/bin/python"
else
  RUNTIME_PYTHON="$PYTHON_BIN"
fi

section "Workspace"
printf 'repo=%s\n' "$PWD"
printf 'python=%s\n' "$RUNTIME_PYTHON"
printf 'config=%s\n' "$CONFIG"
mkdir -p state
printf 'state_dir=state\n'

section "Bootstrap"
if [ "$DO_BOOTSTRAP" -eq 1 ]; then
  PYTHON_BIN="$PYTHON_BIN" VENV_DIR="$VENV_DIR" scripts/bootstrap_env.sh
  RUNTIME_PYTHON="$VENV_DIR/bin/python"
else
  if [ -x "$VENV_DIR/bin/python" ]; then
    printf 'venv=ready (%s)\n' "$VENV_DIR"
  else
    printf 'venv=missing; run scripts/onboard_local.sh --bootstrap\n'
  fi
fi
"$RUNTIME_PYTHON" watchtower.py --version

section "Config"
if [ ! -f "$CONFIG" ] && [ "$DO_WRITE_CONFIG" -eq 1 ]; then
  cp config.example.json "$CONFIG"
  printf 'created=%s from config.example.json\n' "$CONFIG"
fi
if [ -f "$CONFIG" ]; then
  run_optional "validate_config" "$RUNTIME_PYTHON" watchtower.py -c "$CONFIG" --validate-config
else
  printf 'missing=%s\n' "$CONFIG"
  printf 'next=cp config.example.json %s and edit node paths/endpoints\n' "$CONFIG"
fi

section "Local Checks"
run_optional "proto_check" scripts/check_generated_proto.sh
if [ -f "$CONFIG" ]; then
  run_optional "summary" "$RUNTIME_PYTHON" watchtower.py -c "$CONFIG" --summary
  run_optional "prometheus_textfile" "$RUNTIME_PYTHON" watchtower.py -c "$CONFIG" --prometheus
  run_optional "history_multi_node" scripts/export_history_sqlite.py --multi-node-summary --days 7
else
  printf 'summary=skipped; config missing\n'
  printf 'prometheus_textfile=skipped; config missing\n'
fi

section "Launchd"
printf 'manager=scripts/manage_launchd.sh\n'
printf 'managed_services=exporter,status,benchmark,daily,weekly,alerts,smoke\n'
printf 'preview=scripts/manage_launchd.sh --service exporter print\n'
printf 'dry_run=scripts/manage_launchd.sh install\n'
printf 'install_or_repair=make launchd-install\n'
printf 'status=make launchd-status\n'
printf 'legacy_exporter_only=make ensure-exporter\n'

section "Smoke"
if [ "$DO_SMOKE" -eq 1 ]; then
  scripts/smoke_test.sh
else
  printf 'skipped; run scripts/onboard_local.sh --smoke after config is ready\n'
fi

section "Next"
cat <<'EOF'
1. Edit config.json for the local node.
2. Run make validate && make summary.
3. Run make prometheus and confirm state/watchtower.prom exists.
4. Run make history-multi-node after benchmark history exists.
5. Run make launchd-install when ready to install/restart LaunchAgents.
6. Run make smoke before release or host handoff.
EOF
