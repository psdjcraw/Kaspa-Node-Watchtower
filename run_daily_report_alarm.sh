#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p state
tmp_log="$(mktemp state/daily-report-latest.XXXXXX.log)"
full_log="state/daily-report-latest.out.log"

if ./run_daily_report.sh >"$tmp_log" 2>&1; then
  mv "$tmp_log" "$full_log"
else
  status=$?
  mv "$tmp_log" "$full_log"
  printf 'Kaspa Watchtower 일일보고 실패\n'
  printf 'status=error exit_code=%s\n' "$status"
  printf 'log=%s\n\n' "$full_log"
  tail -n 30 "$full_log"
  exit "$status"
fi

python3 - "$full_log" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path


def first_with(lines: list[str], prefix: str) -> str | None:
    return next((line for line in lines if line.startswith(prefix)), None)


path = Path(sys.argv[1])
lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]

generated_at = first_with(lines, "생성시각:")
verdict_line = first_with(lines, "- 판정:") or "- 판정: unknown"
status_line = first_with(lines, "- 상태:") or "- 상태: unknown"
failed_line = first_with(lines, "- 실패 체크:") or "- 실패 체크: unknown"
incident_line = first_with(lines, "- 현재 사고:")
grpc_line = first_with(lines, "- gRPC:")
dag_line = first_with(lines, "- DAG:")
indexer_line = first_with(lines, "- 인덱서:")
history_line = first_with(lines, "- 7일 요약:")
multi_line = first_with(lines, "- 멀티노드:")
integration_line = first_with(lines, "- integrations:")
smoke_line = first_with(lines, "- GitHub smoke:")
codeql_line = first_with(lines, "- GitHub codeql:")

print("Kaspa Watchtower 일일보고")
if generated_at:
    print(generated_at)
for line in (
    verdict_line,
    status_line,
    failed_line,
    incident_line,
    grpc_line,
    dag_line,
    indexer_line,
    history_line,
    multi_line,
    integration_line,
    smoke_line,
    codeql_line,
):
    if line:
        print(line)
print(f"log={path}")
PY
