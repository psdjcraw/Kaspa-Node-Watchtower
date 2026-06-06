#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

ok() {
  printf 'OK %s\n' "$1"
}

"$PYTHON_BIN" -m py_compile watchtower.py kaspa_grpc_probe.py prometheus_file_server.py scripts/upgrade_checkpoint.py scripts/export_history_sqlite.py
ok "Python compile"

"$PYTHON_BIN" watchtower.py --version >/dev/null
ok "watchtower version"

scripts/check_generated_proto.sh >/dev/null
ok "generated protobuf"

"$PYTHON_BIN" -m unittest discover -s tests >/dev/null
ok "unit tests"

python3 -m json.tool grafana/kaspa-watchtower.json >/dev/null
ok "Grafana dashboard JSON"

prometheus/run_rule_tests.sh >/dev/null
ok "Prometheus rule tests"

if [ -f "config.json" ]; then
  "$PYTHON_BIN" watchtower.py -c config.json --validate-config >/dev/null
  ok "config validation"

  "$PYTHON_BIN" watchtower.py -c config.json --summary >/dev/null
  ok "watchtower summary"

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
  ok "SQLite history export"
else
  ok "config.json absent; skipped live checks"
fi
