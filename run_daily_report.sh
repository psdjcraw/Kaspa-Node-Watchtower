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

section "Mainnet Sync Progress"
"$PYTHON_BIN" - "$PYTHON_BIN" <<'PY'
import json
import subprocess
import sys

completed = subprocess.run(
    [sys.argv[1], "watchtower.py", "-c", "config.json", "--json"],
    check=False,
    text=True,
    capture_output=True,
)
if completed.returncode not in (0, 1):
    print(completed.stderr.strip() or "sync progress unavailable")
    raise SystemExit(0)

report = json.loads(completed.stdout)
sync = report.get("sync_progress") or {}
grpc = report.get("grpc_metrics") or {}
print(f"network={grpc.get('network_id')} synced={grpc.get('is_synced')}")
print(f"baseline={sync.get('baseline_checked_at', 'pending')}")
print(f"detail={sync.get('detail', 'unknown')}")
for key in ("daa", "block", "header"):
    delta = sync.get(f"{key}_delta", "unknown")
    rate = sync.get(f"{key}_rate_per_hour", "unknown")
    print(f"{key}_delta={delta} {key}_rate_per_hour={rate}")
PY

section "Benchmark Trend"
"$PYTHON_BIN" watchtower.py -c config.json --benchmark-report

section "Recovery History"
"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

path = Path("state/recovery-history.jsonl")
if not path.exists():
    print("no recovery history")
    raise SystemExit(0)

records = []
for line in path.read_text(encoding="utf-8").splitlines():
    if line.strip():
        records.append(json.loads(line))

if not records:
    print("no recovery history")
else:
    for item in records[-5:]:
        before = item.get("severity_before", "unknown")
        after = item.get("severity_after", "n/a")
        reason = item.get("reason", "")
        failed = ",".join(item.get("failed_checks_before") or []) or "none"
        print(
            f"{item.get('started_at', 'unknown')} "
            f"action={item.get('action', 'unknown')} "
            f"before={before} after={after} failed={failed} reason={reason}"
        )
PY

section "SQLite History"
scripts/export_history_sqlite.py >/dev/null
"$PYTHON_BIN" - <<'PY'
import sqlite3

with sqlite3.connect("state/watchtower-history.sqlite") as connection:
    for table in ("benchmark_snapshots", "upgrade_checkpoints", "recovery_attempts"):
        count = connection.execute(f"select count(*) from {table}").fetchone()[0]
        print(f"{table}={count}")
PY

section "Integrations"
scripts/check_integrations.sh

section "GitHub Actions"
KASPA_WATCHTOWER_GITHUB_WORKFLOW=smoke.yml scripts/check_ci_status.sh
KASPA_WATCHTOWER_GITHUB_WORKFLOW=codeql.yml scripts/check_ci_status.sh

section "Dashboard"
printf 'status_html=%s\n' "state/status.html"
printf 'canvas_html=%s\n' "/Users/psdjc/.openclaw/canvas/kaspa-watchtower/status.html"
printf 'grafana=%s\n' "http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower"
