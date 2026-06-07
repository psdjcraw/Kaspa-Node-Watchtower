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

section "Kaspa Watchtower Weekly Report"
date '+generated_at=%Y-%m-%dT%H:%M:%S%z'

section "Incident Summary"
"$PYTHON_BIN" watchtower.py -c config.json --diagnostics-summary

section "Benchmark Trend"
"$PYTHON_BIN" watchtower.py -c config.json --benchmark-report --benchmark-limit 336

section "Market Snapshot"
"$PYTHON_BIN" watchtower.py --market-summary --market-timeout 5

section "History Summary 7d"
scripts/export_history_sqlite.py --summary --days 7 | sed -n '/^window_days=/,$p'

section "History Summary 30d"
scripts/export_history_sqlite.py --summary --days 30 | sed -n '/^window_days=/,$p'

section "Recent Recovery Attempts"
"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

path = Path("state/recovery-history.jsonl")
if not path.exists():
    print("none")
    raise SystemExit(0)

records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
if not records:
    print("none")
    raise SystemExit(0)

for item in records[-20:]:
    failed = ",".join(item.get("failed_checks_before") or []) or "none"
    print(
        f"{item.get('started_at', 'unknown')} "
        f"action={item.get('action', 'unknown')} "
        f"before={item.get('severity_before', 'unknown')} "
        f"after={item.get('severity_after', 'n/a')} "
        f"failed={failed}"
    )
PY

section "Recent Upgrade Checkpoints"
"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

path = Path("state/upgrade-checkpoints.jsonl")
if not path.exists():
    print("none")
    raise SystemExit(0)

records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
if not records:
    print("none")
    raise SystemExit(0)

for item in records[-10:]:
    print(
        f"{item.get('recorded_at', 'unknown')} "
        f"phase={item.get('phase', 'unknown')} "
        f"label={item.get('label', 'unknown')} "
        f"status={item.get('status', 'unknown')} "
        f"severity={item.get('severity', 'unknown')} "
        f"revision={item.get('git_revision', 'unknown')}"
    )
PY
