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

"$PYTHON_BIN" -m py_compile watchtower.py kaspa_grpc_probe.py prometheus_file_server.py
ok "Python compile"

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

  ./run_watchtower.sh >/dev/null
  ok "alert wrapper"

  ./run_benchmark_snapshot.sh >/dev/null
  ok "benchmark wrapper"

  scripts/check_integrations.sh >/dev/null
  ok "external integrations"

  scripts/check_prometheus_alerts.sh >/dev/null
  ok "Prometheus alert bridge"
else
  ok "config.json absent; skipped live checks"
fi
