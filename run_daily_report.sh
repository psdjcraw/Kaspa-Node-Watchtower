#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

section() {
  printf '\n**%s**\n' "$1"
}

section "Kaspa Watchtower 일일보고"
date '+생성시각: %Y-%m-%d %H:%M:%S %z'

"$PYTHON_BIN" - <<'PY'
import sqlite3
import subprocess
import json
import urllib.error
import urllib.request
from pathlib import Path

import watchtower


def bullet(label: str, value: str) -> None:
    print(f"- {label}: {value}")


def compact_time(value) -> str:
    text = str(value or "none")
    if "T" in text:
        return text.replace("T", " ").split(".")[0]
    return text


def join_or_none(items) -> str:
    return ", ".join(str(item) for item in items if item) or "none"


def format_seconds(value) -> str:
    parsed = watchtower.numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed:.1f}s" if parsed < 60 else f"{parsed / 60:.1f}m"


def check_state(name: str) -> str:
    for check in report.get("checks") or []:
        if check.get("name") == name:
            return "ok" if check.get("ok") else "fail"
    return "missing"


def peer_slo() -> str:
    peers = watchtower.numeric(grpc.get("peer_count"))
    active = watchtower.numeric(grpc.get("active_peers"))
    if peers is None or active is None:
        return "unknown"
    return "ok" if peers >= 1 and active >= 1 else "fail"


def active_prometheus_alerts() -> str:
    url = "http://127.0.0.1:9090/api/v1/alerts"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return f"unavailable ({exc})"
    alerts = [
        item
        for item in payload.get("data", {}).get("alerts", [])
        if item.get("labels", {}).get("service") == "kaspa-watchtower"
    ]
    if not alerts:
        return "none"
    names = [
        f"{item.get('labels', {}).get('alertname', 'unknown')}({item.get('state', 'unknown')})"
        for item in alerts
    ]
    return f"{len(alerts)} " + ", ".join(names)


def docker_lightweight_state() -> str:
    def run(args: list[str]) -> list[str] | None:
        completed = subprocess.run(args, check=False, text=True, capture_output=True)
        if completed.returncode != 0:
            return None
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

    containers = run(["docker", "ps", "--format", "{{.Names}}"])
    volumes = run(["docker", "volume", "ls", "--format", "{{.Name}}"])
    images = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
    if containers is None or volumes is None or images is None:
        return "unavailable"

    def is_indexer_name(value: str) -> bool:
        text = value.lower()
        return (
            "simply-kaspa-indexer" in text
            or "kaspa_watchtower_indexer" in text
            or "kaspa_watchtower_db" in text
            or "kaspa-db-data" in text
        )

    indexer_containers = [item for item in containers if is_indexer_name(item)]
    indexer_volumes = [item for item in volumes if is_indexer_name(item)]
    indexer_images = [item for item in images if "simply-kaspa-indexer" in item.lower()]
    return (
        f"containers={len(indexer_containers)}, "
        f"volumes={len(indexer_volumes)}, "
        f"images={len(indexer_images)}"
    )


config = watchtower.load_config(Path("config.json"))
report, state = watchtower.build_stateful_report(config)
benchmark = watchtower.build_benchmark_summary(
    Path(config.get("benchmark_path") or watchtower.DEFAULT_CONFIG["benchmark_path"]),
    limit=48,
)
failed = watchtower.failed_check_names(report)
incident = report.get("incident") or {}
maintenance = report.get("maintenance") or {}
grpc = report.get("grpc_metrics") or {}
sync = report.get("sync_progress") or {}
progress = report.get("progress") or {}
latest_processed = progress.get("latest_processed") or {}
indexer = report.get("indexer") or {}
watch = report.get("indexer_watch") or {}
disk = report.get("disk") or {}
recovery = report.get("recovery") or {}
recovery_records = watchtower.recent_recovery_records(config)
latest_recovery = recovery_records[-1] if recovery_records else {}
whale = report.get("whale_watch") or {}
whale_summary = watchtower.whale_watch_summary(list(whale.get("events") or []))

if report["status"] == "ok" and not failed:
    verdict = "정상, 조치 불필요"
elif report["severity"] == "critical":
    verdict = "위험, 운영자 확인 필요"
else:
    verdict = "주의, 추적 필요"

section = lambda title: print(f"\n**{title}**")

section("1. 한눈에")
bullet("판정", verdict)
bullet("상태", f"{report.get('status')} / {report.get('severity')} / health {report.get('health_score', 'unknown')}")
bullet("실패 체크", join_or_none(failed))
bullet("Prometheus alerts", active_prometheus_alerts())
bullet(
    "핵심 SLO",
    (
        f"process={check_state('process')}, "
        f"grpc={check_state('grpc_metrics')}, "
        f"relay={check_state('block_progress')}, "
        f"peers={peer_slo()}, "
        f"log={check_state('log_freshness')}, "
        f"disk={check_state('disk_free')}"
    ),
)
bullet("복구 액션", recovery.get("action", "none"))
bullet("최근 복구", f"{latest_recovery.get('action', 'none')} ({latest_recovery.get('operator_reason') or latest_recovery.get('reason') or 'none'})")

section("2. 노드")
bullet(
    "gRPC",
    (
        f"{grpc.get('network_id', 'unknown')}, "
        f"synced={grpc.get('is_synced', 'unknown')}, "
        f"peers={grpc.get('peer_count', 'unknown')} active={grpc.get('active_peers', 'unknown')}"
    ),
)
bullet(
    "DAG",
    (
        f"DAA {grpc.get('virtual_daa_score', 'unknown')}, "
        f"tips {grpc.get('tip_count', 'unknown')}, "
        f"hashrate {watchtower.format_hashrate(grpc.get('network_hashes_per_second'))}"
    ),
)
bullet(
    "처리량",
    (
        f"relay {progress.get('relay_blocks_in_window', 'unknown')} blocks / "
        f"{progress.get('relay_events_in_window', 'unknown')} events, "
        f"tx_rate={watchtower.format_optional_number(latest_processed.get('transactions_per_second'))}/s, "
        f"age={format_seconds(progress.get('latest_processed_age_seconds'))}"
    ),
)
bullet("디스크", f"{watchtower.format_gib(disk.get('free_gb'))} free ({disk.get('free_percent', 'unknown')}%)")
if indexer.get("enabled", False):
    bullet(
        "인덱서",
        (
            f"state={indexer.get('state', 'unknown')}, "
            f"health={indexer.get('health_ok', 'unknown')}, "
            f"syncing={indexer.get('syncing', 'unknown')}, "
            f"watch_events={len(watch.get('events') or [])}, "
            f"new={len(watch.get('new_events') or [])}"
        ),
    )
else:
    bullet("인덱서", "disabled by config; source retained, probes skipped")
bullet("Toccata indexer", watchtower.format_toccata_indexer_daily_summary(report))

section("3. 사고 / 점검")
duration = watchtower.numeric(incident.get("duration_seconds"))
duration_text = "inactive" if duration is None else f"{duration / 60:.1f}m"
causes = incident.get("causes") or report.get("failure_causes") or watchtower.check_failure_causes(report)
bullet("현재 사고", f"active={bool(incident.get('active'))}, duration={duration_text}")
bullet("원인 추정", join_or_none(causes))
bullet("마지막 해결", compact_time((state.get("last_incident") or {}).get("resolved_at")))
bullet(
    "점검 모드",
    (
        f"active={bool(maintenance.get('active'))}, "
        f"critical_only={maintenance.get('critical_only', True)}, "
        f"until={compact_time(maintenance.get('mute_until') if maintenance.get('active') else 'none')}"
    ),
)

section("4. 최근 추세")
bullet(
    "벤치마크",
    (
        f"{benchmark.get('window', 'unknown')} / "
        f"snapshots={benchmark.get('snapshots', 'unknown')}, "
        f"ok={watchtower.format_ratio(benchmark.get('ok_ratio'))}"
    ),
)
bullet(
    "증가량",
    (
        f"DAA {watchtower.format_optional_number(benchmark.get('daa_delta'))}, "
        f"blocks {watchtower.format_optional_number(benchmark.get('block_delta'))}, "
        f"relay avg {benchmark.get('relay_rate', 'unknown')}"
    ),
)
bullet(
    "디스크/최저치",
    (
        f"peers {watchtower.format_optional_number(benchmark.get('min_peer_count'))}, "
        f"disk_min {watchtower.format_gib(benchmark.get('min_disk_free_gb'))}, "
        f"disk_delta {benchmark.get('disk_delta', 'unknown')}"
    ),
)
if benchmark.get("trend_note") and benchmark.get("trend_note") != "none":
    bullet("추세 메모", benchmark.get("trend_note"))

section("5. 동기화")
bullet("네트워크", f"{grpc.get('network_id', 'unknown')}, synced={grpc.get('is_synced', 'unknown')}")
bullet("진행", sync.get("detail", "unknown"))
bullet(
    "속도",
    (
        f"DAA {watchtower.format_optional_rate(sync.get('daa_rate_per_hour'))}/h, "
        f"blocks {watchtower.format_optional_rate(sync.get('block_rate_per_hour'))}/h, "
        f"headers {watchtower.format_optional_rate(sync.get('header_rate_per_hour'))}/h"
    ),
)

section("6. Whale Watch")
bullet("상태", f"enabled={whale.get('enabled', False)}, ok={whale.get('ok', False)}, threshold={watchtower.format_kas(whale.get('min_amount_sompi'))}")
bullet(
    "24h",
    (
        f"count={whale_summary.get('count_24h', 0)}, "
        f"confirmed={whale_summary.get('confirmed_24h', 0)}, "
        f"volume={watchtower.format_kas(whale_summary.get('volume_24h_sompi'))}"
    ),
)
bullet("최근", f"tx={watchtower.short_hash(whale_summary.get('latest_tx_id'))}, amount={watchtower.format_kas(whale_summary.get('latest_amount_sompi'))}")

print("\n**7. 마켓**")
completed = subprocess.run(
    [
        ".venv/bin/python" if Path(".venv/bin/python").exists() else "python3",
        "watchtower.py",
        "-c",
        "config.json",
        "--market-snapshot",
        "--market-timeout",
        "5",
    ],
    check=False,
    text=True,
    capture_output=True,
)
market_lines = [line for line in completed.stdout.splitlines() if line and not line.startswith("Market snapshot saved:")]
if completed.returncode == 0 and market_lines:
    for line in market_lines[:6]:
        print(f"- {line}")
else:
    print(f"- unavailable: {(completed.stderr or completed.stdout).strip() or 'unknown'}")

print("\n**8. 기록 / 외부상태**")
try:
    subprocess.run(["scripts/export_history_sqlite.py"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with sqlite3.connect("state/watchtower-history.sqlite") as connection:
        benchmark_count = connection.execute("select count(*) from benchmark_snapshots").fetchone()[0]
        recovery_count = connection.execute("select count(*) from recovery_attempts").fetchone()[0]
        upgrade_count = connection.execute("select count(*) from upgrade_checkpoints").fetchone()[0]
    bullet("SQLite", f"benchmarks={benchmark_count}, recoveries={recovery_count}, upgrades={upgrade_count}")
except Exception as exc:
    bullet("SQLite", f"unavailable ({exc})")

history = subprocess.run(
    ["scripts/export_history_sqlite.py", "--summary", "--days", "7"],
    check=False,
    text=True,
    capture_output=True,
)
history_fields = {}
for line in history.stdout.splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        history_fields[key] = value
bullet(
    "7일 요약",
    (
        f"ok={history_fields.get('benchmark_ok_ratio', 'unknown')}, "
        f"warn={history_fields.get('benchmark_warn_snapshots', 'unknown')}, "
        f"critical={history_fields.get('benchmark_critical_snapshots', 'unknown')}, "
        f"market_risk={history_fields.get('market_risk', 'unknown')}"
    ),
)

multi = subprocess.run(
    ["scripts/export_history_sqlite.py", "--multi-node-summary", "--days", "7"],
    check=False,
    text=True,
    capture_output=True,
)
multi_fields = {}
for line in multi.stdout.splitlines():
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            multi_fields[key] = value
bullet(
    "멀티노드",
    (
        f"verdict={multi_fields.get('verdict', 'unknown')}, "
        f"nodes={multi_fields.get('nodes', 'unknown')}, "
        f"lagging={multi_fields.get('lagging_nodes', 'unknown')}, "
        f"risk={multi_fields.get('risk_nodes', 'unknown')}"
    ),
)
bullet("Docker/indexer", docker_lightweight_state())
PY

section "9. 통합 / CI"
if scripts/check_integrations.sh >/tmp/kaspa-watchtower-integrations.out 2>&1; then
  printf -- '- integrations: OK\n'
else
  printf -- '- integrations: FAILED\n'
  sed -n '1,3p' /tmp/kaspa-watchtower-integrations.out | sed 's/^/  /'
fi

if KASPA_WATCHTOWER_GITHUB_WORKFLOW=smoke.yml scripts/check_ci_status.sh >/tmp/kaspa-watchtower-ci-smoke.out 2>&1; then
  printf -- '- GitHub smoke: OK\n'
else
  printf -- '- GitHub smoke: FAILED\n'
  sed -n '1,2p' /tmp/kaspa-watchtower-ci-smoke.out | sed 's/^/  /'
fi

if KASPA_WATCHTOWER_GITHUB_WORKFLOW=codeql.yml scripts/check_ci_status.sh >/tmp/kaspa-watchtower-ci-codeql.out 2>&1; then
  printf -- '- GitHub codeql: OK\n'
else
  printf -- '- GitHub codeql: FAILED\n'
  sed -n '1,2p' /tmp/kaspa-watchtower-ci-codeql.out | sed 's/^/  /'
fi

section "10. 링크"
printf -- '- status_html: %s\n' "state/status.html"
printf -- '- canvas_html: %s\n' "/Users/psdjc/.openclaw/canvas/kaspa-watchtower/status.html"
printf -- '- grafana: %s\n' "http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower"
