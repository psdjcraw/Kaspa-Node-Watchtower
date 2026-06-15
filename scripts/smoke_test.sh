#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ok() {
  printf 'OK %s\n' "$1"
}

run_summary_smoke() {
  local output
  local status

  set +e
  output="$("$PYTHON_BIN" watchtower.py -c config.json --summary 2>&1)"
  status=$?
  set -e

  if [ "$status" -ne 0 ] && [ "$status" -ne 1 ]; then
    printf '%s\n' "$output" >&2
    return "$status"
  fi
  if ! grep -q '^Kaspa watchtower summary:' <<<"$output"; then
    printf '%s\n' "$output" >&2
    return 1
  fi
}

"$PYTHON_BIN" -m py_compile watchtower.py kaspa_grpc_probe.py prometheus_file_server.py scripts/upgrade_checkpoint.py scripts/export_history_sqlite.py
ok "Python compile"

bash -n scripts/onboard_local.sh
ok "onboarding script syntax"

bash -n scripts/manage_launchd.sh
scripts/manage_launchd.sh --service exporter print >/dev/null
scripts/manage_launchd.sh --service exporter install >/dev/null
ok "launchd manager dry-run"

"$PYTHON_BIN" watchtower.py --version >/dev/null
ok "watchtower version"

scripts/check_generated_proto.sh >/dev/null
ok "generated protobuf"

"$PYTHON_BIN" -m unittest discover -s tests >/dev/null
ok "unit tests"

scripts/package_release.sh --dist-dir "$TMP_DIR/dist" --label smoke >/dev/null
test -s "$TMP_DIR/dist/kaspa-node-watchtower-smoke.tar.gz"
test -s "$TMP_DIR/dist/kaspa-node-watchtower-smoke.tar.gz.sha256"
tar -tzf "$TMP_DIR/dist/kaspa-node-watchtower-smoke.tar.gz" | grep -q 'PACKAGE-MANIFEST.json'
ok "release package"

if command -v ruby >/dev/null 2>&1; then
  ruby -c packaging/homebrew/kaspa-node-watchtower.rb >/dev/null
  ok "Homebrew formula syntax"
else
  ok "Homebrew formula syntax skipped"
fi

python3 -m json.tool grafana/kaspa-watchtower.json >/dev/null
ok "Grafana dashboard JSON"

prometheus/run_rule_tests.sh >/dev/null
ok "Prometheus rule tests"

if [ -f "config.json" ]; then
  "$PYTHON_BIN" watchtower.py -c config.json --validate-config >/dev/null
  ok "config validation"

  run_summary_smoke
  ok "watchtower summary"

  "$PYTHON_BIN" watchtower.py -c config.json --diagnostics-summary >/dev/null
  ok "diagnostics summary"

  "$PYTHON_BIN" watchtower.py -c config.json --incident-report >/dev/null
  ok "incident report"

  "$PYTHON_BIN" watchtower.py -c config.json --discord-command status >/dev/null
  ok "Discord status command"

  "$PYTHON_BIN" watchtower.py -c config.json --discord-command incidents >/dev/null
  ok "Discord incidents command"

  "$PYTHON_BIN" watchtower.py -c config.json --prometheus >/dev/null
  test -s state/watchtower.prom
  ok "Prometheus textfile"

  "$PYTHON_BIN" watchtower.py -c config.json --prune-state >/dev/null
  ok "state retention"

  scripts/simulate_failures.sh >/dev/null
  ok "failure simulations"

  scripts/simulate_exporter_failure.sh >/dev/null
  ok "exporter failure simulation"

  ./run_watchtower.sh >/dev/null
  ok "alert wrapper"

  ./run_benchmark_snapshot.sh >/dev/null
  ok "benchmark wrapper"

  ./run_weekly_report.sh >/dev/null
  ok "weekly report"

  if [ "${KASPA_WATCHTOWER_SMOKE_INTEGRATIONS:-0}" = "1" ]; then
    scripts/check_integrations.sh >/dev/null
    ok "external integrations"
  else
    ok "external integrations skipped"
  fi

  scripts/check_prometheus_alerts.sh >/dev/null
  ok "Prometheus alert bridge"

  scripts/export_history_sqlite.py >/dev/null
  test -s state/watchtower-history.sqlite
  scripts/export_history_sqlite.py --summary --days 7 >/dev/null
  scripts/export_history_sqlite.py --multi-node-summary --days 7 >/dev/null
  scripts/export_history_sqlite.py --archive-dir state/history-archives --archive-label smoke >/dev/null
  test -s state/history-archives/smoke/manifest.json
  scripts/upload_archive.sh --source state/history-archives/smoke --target "$TMP_DIR/uploaded" >/dev/null
  test -s "$TMP_DIR/uploaded/smoke/manifest.json"
  "$PYTHON_BIN" - <<'PY'
import sqlite3

with sqlite3.connect("state/watchtower-history.sqlite") as connection:
    tables = {
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type = 'table'"
        )
    }
assert "recovery_attempts" in tables
PY
  ok "history export and archive"
else
  ok "config.json absent; skipped live checks"
fi
