#!/usr/bin/env python3
"""Small local health reporter for a Kaspa node."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


VERSION = "0.6.0"

DEFAULT_CONFIG = {
    "config_version": 1,
    "node_name": "kaspa-local",
    "process_match": "kaspad",
    "log_scan_bytes": 100_000_000,
    "log_path": "",
    "data_dir": "",
    "rpc_endpoint": "",
    "grpc_endpoint": "",
    "state_path": "state/watchtower-state.json",
    "status_page_path": "state/status.html",
    "canvas_status_page_path": "",
    "benchmark_path": "state/benchmarks.jsonl",
    "prometheus_metrics_path": "state/watchtower.prom",
    "recovery_history_path": "state/recovery-history.jsonl",
    "retention": {
        "state_history_entries": 100,
        "benchmark_entries": 1000,
    },
    "thresholds": {
        "alert_repeat_minutes": 60,
        "stale_log_minutes": 15,
        "stale_processed_stats_minutes": 3,
        "progress_window_minutes": 10,
        "min_relay_blocks_in_window": 1,
        "min_peer_count": 1,
        "require_grpc_metrics": True,
        "require_synced": True,
        "require_relay_progress_when_unsynced": False,
        "require_sync_progress_when_unsynced": True,
        "sync_progress_stall_minutes": 30,
        "min_sync_daa_delta": 1,
        "min_sync_block_delta": 1,
        "min_sync_header_delta": 1,
        "disk_free_gb_min": 20,
        "disk_free_percent_min": 5,
        "require_rpc": True,
    },
    "recovery": {
        "mode": "manual",
        "restart_command": [],
        "post_recovery_wait_seconds": 20,
    },
}


@dataclass(frozen=True)
class IbdCompletion:
    timestamp: str
    blocks: int


@dataclass(frozen=True)
class RelayAccepted:
    timestamp: dt.datetime
    blocks: int
    line: str


@dataclass(frozen=True)
class ProcessedStats:
    timestamp: dt.datetime
    blocks: int
    headers: int
    seconds: float
    transactions: int
    line: str


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


CRITICAL_CHECKS = {
    "process",
    "data_dir",
    "rpc_tcp",
    "grpc_metrics",
    "sync_status",
    "peer_count",
    "log_file",
}


def summarize_severity(checks: list[Check]) -> str:
    failed = [check.name for check in checks if not check.ok]
    if not failed:
        return "ok"
    if any(name in CRITICAL_CHECKS for name in failed):
        return "critical"
    return "warn"


def recalculate_report_health(report: dict[str, Any], config: dict) -> None:
    checks = [
        Check(str(check["name"]), bool(check["ok"]), str(check["detail"]))
        for check in report.get("checks", [])
    ]
    severity = summarize_severity(checks)
    report["severity"] = severity
    report["status"] = "ok" if severity == "ok" else "alert"
    report["recovery"] = recovery_plan(config, severity)


def load_config(path: Path | None) -> dict:
    config = dict(DEFAULT_CONFIG)
    if path is None:
        return config
    with path.open("r", encoding="utf-8") as handle:
        config.update(json.load(handle))
    return config


def run_command(args: list[str]) -> str:
    completed = subprocess.run(args, check=False, text=True, capture_output=True)
    return completed.stdout.strip()


def run_command_result(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, text=True, capture_output=True)


def find_processes(match: str) -> list[str]:
    output = run_command(["ps", "-axo", "pid,pcpu,pmem,etime,command"])
    lines = []
    for line in output.splitlines()[1:]:
        if match in line and "watchtower.py" not in line and "tail -f" not in line:
            lines.append(line.strip())
    return lines


def dir_size(path: str) -> str:
    if not path:
        return "not configured"
    target = Path(path)
    if not target.exists():
        return "missing"
    output = run_command(["du", "-sh", str(target)])
    return output.split()[0] if output else "unknown"


def disk_usage(path: str) -> dict[str, Any]:
    if not path:
        return {"configured": False}
    target = Path(path)
    if not target.exists():
        return {"configured": True, "exists": False}
    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024**3)
    total_gb = usage.total / (1024**3)
    free_percent = (usage.free / usage.total) * 100 if usage.total else 0
    return {
        "configured": True,
        "exists": True,
        "free_gb": round(free_gb, 2),
        "total_gb": round(total_gb, 2),
        "free_percent": round(free_percent, 2),
    }


def tail_lines(path: Path, max_bytes: int = 2_000_000) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        data = handle.read()
    return data.decode("utf-8", errors="replace").splitlines()


def parse_ibd_completions(lines: Iterable[str]) -> list[IbdCompletion]:
    pattern = re.compile(r"^(\S+ \S+) .* IBD: Processed (\d+) blocks \(100%\)")
    completions = []
    for line in lines:
        match = pattern.search(line)
        if match:
            completions.append(IbdCompletion(match.group(1), int(match.group(2))))
    return completions


def parse_log_timestamp(line: str) -> dt.datetime | None:
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+[+-]\d{2}:\d{2})", line)
    if not match:
        return None
    return dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f%z")


def latest_log_timestamp(lines: Iterable[str]) -> dt.datetime | None:
    latest = None
    for line in lines:
        parsed = parse_log_timestamp(line)
        if parsed is not None:
            latest = parsed
    return latest


def check_tcp_endpoint(endpoint: str, timeout: float = 2.0) -> dict[str, Any]:
    if not endpoint:
        return {"configured": False, "ok": False, "detail": "not configured"}
    if ":" not in endpoint:
        return {"configured": True, "ok": False, "detail": "invalid endpoint"}
    host, port_text = endpoint.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError:
        return {"configured": True, "ok": False, "detail": "invalid port"}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"configured": True, "ok": True, "detail": f"tcp connect ok to {endpoint}"}
    except OSError as exc:
        return {"configured": True, "ok": False, "detail": f"tcp connect failed to {endpoint}: {exc}"}


def fetch_optional_grpc_metrics(endpoint: str) -> dict[str, Any]:
    if not endpoint:
        return {"ok": False, "configured": False, "detail": "not configured"}
    try:
        from kaspa_grpc_probe import fetch_grpc_metrics
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "detail": f"gRPC probe unavailable: {exc}",
        }
    metrics = fetch_grpc_metrics(endpoint)
    metrics["configured"] = True
    if not metrics.get("ok"):
        metrics["detail"] = metrics.get("error") or "gRPC probe failed"
    return metrics


def parse_trusted_blocks(lines: Iterable[str]) -> int:
    pattern = re.compile(r"Starting to process (\d+) trusted blocks")
    total = 0
    for line in lines:
        match = pattern.search(line)
        if match:
            total += int(match.group(1))
    return total


def parse_relay_accepted(lines: Iterable[str]) -> list[RelayAccepted]:
    pattern = re.compile(r"Accepted (\d+) blocks? .* via relay")
    accepted = []
    for line in lines:
        timestamp = parse_log_timestamp(line)
        if timestamp is None:
            continue
        match = pattern.search(line)
        if match:
            accepted.append(RelayAccepted(timestamp, int(match.group(1)), line))
    return accepted


def parse_processed_stats(lines: Iterable[str]) -> list[ProcessedStats]:
    pattern = re.compile(
        r"Processed (\d+) blocks and (\d+) headers in the last ([0-9.]+)s "
        r"\((\d+) transactions;"
    )
    stats = []
    for line in lines:
        timestamp = parse_log_timestamp(line)
        if timestamp is None:
            continue
        match = pattern.search(line)
        if match:
            stats.append(
                ProcessedStats(
                    timestamp=timestamp,
                    blocks=int(match.group(1)),
                    headers=int(match.group(2)),
                    seconds=float(match.group(3)),
                    transactions=int(match.group(4)),
                    line=line,
                )
            )
    return stats


def latest_matching(lines: Iterable[str], needle: str) -> str | None:
    found = None
    for line in lines:
        if needle in line:
            found = line
    return found


def format_processes(processes: list[str]) -> str:
    if not processes:
        return "not running"
    return "\n".join(f"  {line}" for line in processes)


def build_report(config: dict) -> dict[str, Any]:
    log_path = Path(config.get("log_path") or "")
    processes = find_processes(config["process_match"])
    thresholds = config.get("thresholds", {})
    grpc_endpoint = config.get("grpc_endpoint") or config.get("rpc_endpoint") or ""
    disk = disk_usage(config.get("data_dir", ""))
    rpc = check_tcp_endpoint(config.get("rpc_endpoint") or "")
    grpc_metrics = fetch_optional_grpc_metrics(grpc_endpoint)

    checks = [
        Check("process", bool(processes), "running" if processes else "not running"),
        Check("data_dir", disk.get("exists", False), dir_size(config.get("data_dir", ""))),
    ]

    require_rpc = bool(thresholds.get("require_rpc", True))
    if rpc["configured"] or require_rpc:
        checks.append(Check("rpc_tcp", bool(rpc.get("ok")), rpc["detail"]))

    require_grpc_metrics = bool(thresholds.get("require_grpc_metrics", True))
    if grpc_metrics.get("configured") or require_grpc_metrics:
        checks.append(
            Check(
                "grpc_metrics",
                bool(grpc_metrics.get("ok")),
                "read ok" if grpc_metrics.get("ok") else grpc_metrics.get("detail", "failed"),
            )
        )
    if grpc_metrics.get("ok"):
        is_synced = bool(grpc_metrics.get("is_synced"))
        require_synced = bool(thresholds.get("require_synced", True))
        checks.append(
            Check(
                "sync_status",
                is_synced or not require_synced,
                f"is_synced={is_synced} require_synced={require_synced}",
            )
        )
        min_peer_count = int(thresholds.get("min_peer_count", 1))
        checks.append(
            Check(
                "peer_count",
                int(grpc_metrics.get("peer_count") or 0) >= min_peer_count,
                (
                    f"{int(grpc_metrics.get('peer_count') or 0)} peers "
                    f"(active={grpc_metrics.get('active_peers')})"
                ),
            )
        )

    min_free_gb = float(thresholds.get("disk_free_gb_min", 0))
    min_free_percent = float(thresholds.get("disk_free_percent_min", 0))
    if disk.get("exists"):
        free_gb = float(disk["free_gb"])
        free_percent = float(disk["free_percent"])
        disk_ok = free_gb >= min_free_gb and free_percent >= min_free_percent
        checks.append(
            Check(
                "disk_free",
                disk_ok,
                f"{free_gb:.2f} GiB free ({free_percent:.2f}%)",
            )
        )

    log = {"path": str(log_path), "exists": log_path.exists()}
    completions: list[IbdCompletion] = []
    trusted_blocks = 0
    latest_relay = None
    latest_throughput = None
    progress: dict[str, Any] = {
        "window_minutes": float(thresholds.get("progress_window_minutes", 10)),
        "relay_blocks_in_window": 0,
        "relay_events_in_window": 0,
        "latest_relay_age_seconds": None,
        "latest_processed": None,
        "latest_processed_age_seconds": None,
        "relay_samples": [],
        "processed_samples": [],
    }
    if log_path.exists():
        lines = tail_lines(log_path, int(config["log_scan_bytes"]))
        completions = parse_ibd_completions(lines)
        trusted_blocks = parse_trusted_blocks(lines)
        latest_relay = latest_matching(lines, " via relay")
        latest_throughput = latest_matching(lines, "Tx throughput stats:")
        relay_events = parse_relay_accepted(lines)
        processed_stats = parse_processed_stats(lines)
        latest_timestamp = latest_log_timestamp(lines)
        if latest_timestamp is not None:
            now = dt.datetime.now(latest_timestamp.tzinfo)
            age_seconds = max(0.0, (now - latest_timestamp).total_seconds())
            log["latest_timestamp"] = latest_timestamp.isoformat()
            log["age_seconds"] = round(age_seconds, 1)
            stale_limit = float(thresholds.get("stale_log_minutes", 15)) * 60
            checks.append(
                Check(
                    "log_freshness",
                    age_seconds <= stale_limit,
                    f"latest log is {age_seconds:.1f}s old",
                )
            )
            progress_window = float(thresholds.get("progress_window_minutes", 10))
            cutoff = now - dt.timedelta(minutes=progress_window)
            recent_relay_events = [event for event in relay_events if event.timestamp >= cutoff]
            recent_relay_blocks = sum(event.blocks for event in recent_relay_events)
            progress["relay_samples"] = [
                {
                    "timestamp": event.timestamp.isoformat(),
                    "blocks": event.blocks,
                }
                for event in recent_relay_events[-48:]
            ]
            latest_relay_event = relay_events[-1] if relay_events else None
            if latest_relay_event is not None:
                progress["latest_relay_timestamp"] = latest_relay_event.timestamp.isoformat()
                progress["latest_relay_blocks"] = latest_relay_event.blocks
                progress["latest_relay_age_seconds"] = round(
                    max(0.0, (now - latest_relay_event.timestamp).total_seconds()),
                    1,
                )
            progress["relay_blocks_in_window"] = recent_relay_blocks
            progress["relay_events_in_window"] = len(recent_relay_events)
            if processed_stats:
                progress["processed_samples"] = [
                    {
                        "timestamp": item.timestamp.isoformat(),
                        "blocks": item.blocks,
                        "headers": item.headers,
                        "seconds": item.seconds,
                        "transactions": item.transactions,
                        "blocks_per_second": None if item.seconds <= 0 else item.blocks / item.seconds,
                        "headers_per_second": None if item.seconds <= 0 else item.headers / item.seconds,
                        "transactions_per_second": None if item.seconds <= 0 else item.transactions / item.seconds,
                    }
                    for item in processed_stats[-36:]
                ]
                latest_processed = processed_stats[-1]
                progress["latest_processed"] = {
                    "timestamp": latest_processed.timestamp.isoformat(),
                    "blocks": latest_processed.blocks,
                    "headers": latest_processed.headers,
                    "seconds": latest_processed.seconds,
                    "transactions": latest_processed.transactions,
                    "blocks_per_second": None
                    if latest_processed.seconds <= 0
                    else latest_processed.blocks / latest_processed.seconds,
                    "headers_per_second": None
                    if latest_processed.seconds <= 0
                    else latest_processed.headers / latest_processed.seconds,
                    "transactions_per_second": None
                    if latest_processed.seconds <= 0
                    else latest_processed.transactions / latest_processed.seconds,
                    "line": latest_processed.line,
                }
                progress["latest_processed_age_seconds"] = round(
                    max(0.0, (now - latest_processed.timestamp).total_seconds()),
                    1,
                )
            processed_stats_limit = float(thresholds.get("stale_processed_stats_minutes", 3)) * 60
            processed_stats_age = progress.get("latest_processed_age_seconds")
            require_processed_stats = bool(grpc_metrics.get("is_synced")) if grpc_metrics.get("ok") else False
            if require_processed_stats:
                processed_ok = (
                    processed_stats_age is not None and processed_stats_age <= processed_stats_limit
                )
                if processed_stats_age is None:
                    processed_detail = (
                        "no processed stats observed in readable kaspad log; "
                        f"threshold={processed_stats_limit:.0f}s"
                    )
                else:
                    processed_detail = (
                        f"latest processed stats are {processed_stats_age:.1f}s old "
                        f"(threshold={processed_stats_limit:.0f}s); "
                        "inspect kaspad processed-stats log output"
                    )
                checks.append(
                    Check(
                        "processed_stats_freshness",
                        processed_ok,
                        processed_detail,
                    )
                )
            min_relay_blocks = int(thresholds.get("min_relay_blocks_in_window", 1))
            require_unsynced_progress = bool(
                thresholds.get("require_relay_progress_when_unsynced", False)
            )
            is_synced = bool(grpc_metrics.get("is_synced")) if grpc_metrics.get("ok") else True
            block_progress_ok = recent_relay_blocks >= min_relay_blocks
            block_progress_detail = (
                f"{recent_relay_blocks} relay blocks in "
                f"{progress_window:g}m window "
                f"({len(recent_relay_events)} events)"
            )
            if not is_synced and not require_unsynced_progress:
                block_progress_ok = True
                block_progress_detail = (
                    f"skipped while unsynced; {block_progress_detail}"
                )
            checks.append(
                Check(
                    "block_progress",
                    block_progress_ok,
                    block_progress_detail,
                )
            )
    else:
        checks.append(Check("log_file", False, f"missing ({log_path})"))

    severity = summarize_severity(checks)
    status_text = "ok" if severity == "ok" else "alert"
    report = {
        "config_version": config.get("config_version", DEFAULT_CONFIG["config_version"]),
        "node_name": config["node_name"],
        "status": status_text,
        "severity": severity,
        "checked_at": dt.datetime.now().astimezone().isoformat(),
        "process_match": config["process_match"],
        "processes": processes,
        "rpc_endpoint": config.get("rpc_endpoint") or "",
        "rpc": rpc,
        "grpc_endpoint": grpc_endpoint,
        "grpc_metrics": grpc_metrics,
        "data_dir": config.get("data_dir") or "",
        "data_dir_size": dir_size(config.get("data_dir", "")),
        "disk": disk,
        "log": log,
        "checks": [check.as_dict() for check in checks],
        "ibd_completed_blocks": sum(item.blocks for item in completions),
        "ibd_completion_events": len(completions),
        "latest_ibd_completion": completions[-1].__dict__ if completions else None,
        "trusted_blocks_observed": trusted_blocks,
        "latest_relay_block": latest_relay,
        "latest_throughput": latest_throughput,
        "progress": progress,
        "monitoring": {
            "require_synced": bool(thresholds.get("require_synced", True)),
            "require_relay_progress_when_unsynced": bool(
                thresholds.get("require_relay_progress_when_unsynced", False)
            ),
            "require_sync_progress_when_unsynced": bool(
                thresholds.get("require_sync_progress_when_unsynced", True)
            ),
            "sync_progress_stall_minutes": float(thresholds.get("sync_progress_stall_minutes", 30)),
        },
        "recovery": recovery_plan(config, severity),
    }
    return report


def build_stateful_report(config: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    report = build_report(config)
    state_path = Path(config.get("state_path") or DEFAULT_CONFIG["state_path"])
    state = load_state(state_path)
    apply_stateful_checks(report, state, config)
    return report, state


def status(config: dict) -> int:
    report, _state = build_stateful_report(config)

    print(f"Kaspa Node Watchtower: {report['node_name']}")
    print(f"Status: {report['status']}")
    print(f"Severity: {report['severity']}")
    print(f"Checked at: {report['checked_at']}")
    print(f"RPC endpoint: {report['rpc_endpoint'] or 'not configured'}")
    print(f"Process match: {report['process_match']}")
    print("Processes:")
    print(format_processes(report["processes"]))
    print(f"Data dir size: {report['data_dir_size']}")
    if report["disk"].get("exists"):
        print(
            "Disk free: "
            f"{report['disk']['free_gb']:.2f} GiB "
            f"({report['disk']['free_percent']:.2f}%)"
        )
    print(f"Log file: {report['log']['path']}")
    if not report["log"]["exists"]:
        print("Log status: missing")
    elif "latest_timestamp" in report["log"]:
        print(f"Latest log timestamp: {report['log']['latest_timestamp']}")
        print(f"Latest log age: {report['log']['age_seconds']}s")
    print("Checks:")
    for check in report["checks"]:
        mark = "OK" if check["ok"] else "ALERT"
        print(f"  {mark} {check['name']}: {check['detail']}")
    print(f"IBD/catch-up completed block bodies: {report['ibd_completed_blocks']:,}")
    print(f"IBD completion events: {report['ibd_completion_events']}")
    latest_ibd = report["latest_ibd_completion"]
    if latest_ibd:
        print(f"Latest IBD completion: {latest_ibd['timestamp']} ({latest_ibd['blocks']:,} blocks)")
    print(f"Trusted blocks observed: {report['trusted_blocks_observed']:,}")
    if report["latest_relay_block"]:
        print(f"Latest relay block: {report['latest_relay_block']}")
    if report["latest_throughput"]:
        print(f"Latest throughput: {report['latest_throughput']}")
    grpc_metrics = report.get("grpc_metrics") or {}
    if grpc_metrics.get("ok"):
        print(
            "gRPC metrics: "
            f"synced={grpc_metrics.get('is_synced')} "
            f"peers={grpc_metrics.get('peer_count')} "
            f"network={grpc_metrics.get('network_id')} "
            f"daa={grpc_metrics.get('virtual_daa_score')} "
            f"mempool={grpc_metrics.get('mempool_size')} "
            f"tips={grpc_metrics.get('tip_count')} "
            f"pruning={short_hash(grpc_metrics.get('pruning_point_hash'))}"
        )
    progress = report["progress"]
    print(
        "Relay progress: "
        f"{progress['relay_blocks_in_window']} blocks / "
        f"{progress['relay_events_in_window']} events in "
        f"{progress['window_minutes']:g}m"
    )
    latest_processed = progress.get("latest_processed")
    if latest_processed:
        print(
            "Latest processed stats: "
            f"{latest_processed['blocks']} blocks, "
            f"{latest_processed['headers']} headers, "
            f"{latest_processed['transactions']} tx in "
            f"{latest_processed['seconds']}s"
        )

    return 0 if report["status"] == "ok" else 1


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def failed_check_names(report: dict[str, Any]) -> list[str]:
    return [check["name"] for check in report["checks"] if not check["ok"]]


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_delta(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value.is_integer():
        return f"{int(value):+d}"
    return f"{value:+.2f}"


def format_optional_rate(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed:.2f}/h"


def format_optional_number(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return str(int(parsed)) if parsed.is_integer() else f"{parsed:.2f}"


def format_ratio(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed * 100:.1f}%"


def format_gib(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed:.2f} GiB"


def format_hashrate(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    units = ["H/s", "KH/s", "MH/s", "GH/s", "TH/s", "PH/s", "EH/s"]
    index = 0
    while abs(parsed) >= 1000 and index < len(units) - 1:
        parsed /= 1000
        index += 1
    if abs(parsed) >= 100:
        return f"{parsed:.0f} {units[index]}"
    if abs(parsed) >= 10:
        return f"{parsed:.1f} {units[index]}"
    return f"{parsed:.2f} {units[index]}"


def short_hash(value: Any, length: int = 12) -> str:
    text = str(value or "")
    return text[:length] if text else "unknown"


def format_processed_progress(progress: dict[str, Any]) -> str:
    latest = progress.get("latest_processed") or {}
    tx_rate = numeric(latest.get("transactions_per_second"))
    tx_rate_text = "unknown" if tx_rate is None else f"{tx_rate:.2f}/s"
    age = numeric(progress.get("latest_processed_age_seconds"))
    age_text = "unknown" if age is None else f"{age:g}s"
    seconds = latest.get("seconds")
    window_text = "unknown" if seconds is None else f"{seconds}s"
    return (
        f"tx_rate={tx_rate_text} "
        f"age={age_text} "
        f"tx={latest.get('transactions', 'unknown')} "
        f"blocks={latest.get('blocks', 'unknown')} "
        f"window={window_text}"
    )


def history_item(report: dict[str, Any]) -> dict[str, Any]:
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
    latest_processed = progress.get("latest_processed") or {}
    return {
        "checked_at": report["checked_at"],
        "status": report["status"],
        "severity": report["severity"],
        "failed_checks": failed_check_names(report),
        "peer_count": grpc_metrics.get("peer_count"),
        "is_synced": grpc_metrics.get("is_synced"),
        "network_id": grpc_metrics.get("network_id"),
        "virtual_daa_score": grpc_metrics.get("virtual_daa_score"),
        "block_count": grpc_metrics.get("block_count"),
        "header_count": grpc_metrics.get("header_count"),
        "mempool_size": grpc_metrics.get("mempool_size"),
        "network_hashes_per_second": grpc_metrics.get("network_hashes_per_second"),
        "relay_blocks_in_window": progress.get("relay_blocks_in_window"),
        "latest_processed_age_seconds": progress.get("latest_processed_age_seconds"),
        "latest_processed_transactions_per_second": latest_processed.get("transactions_per_second"),
        "latest_processed_transactions": latest_processed.get("transactions"),
        "latest_processed_blocks": latest_processed.get("blocks"),
        "latest_processed_seconds": latest_processed.get("seconds"),
    }


def apply_stateful_checks(report: dict[str, Any], state: dict[str, Any], config: dict) -> None:
    thresholds = config.get("thresholds", {})
    grpc_metrics = report.get("grpc_metrics") or {}
    if not grpc_metrics.get("ok"):
        return
    if bool(grpc_metrics.get("is_synced")):
        return
    if not bool(thresholds.get("require_sync_progress_when_unsynced", True)):
        return

    checked_at = parse_iso_datetime(report.get("checked_at"))
    network_id = grpc_metrics.get("network_id")
    if checked_at is None or not network_id:
        return

    stall_minutes = float(thresholds.get("sync_progress_stall_minutes", 30))
    cutoff = checked_at - dt.timedelta(minutes=stall_minutes)
    candidates = []
    for item in state.get("history", []):
        if item.get("network_id") != network_id:
            continue
        item_at = parse_iso_datetime(item.get("checked_at"))
        if item_at is not None and item_at <= cutoff:
            candidates.append((item_at, item))

    if not candidates:
        detail = f"baseline pending for {stall_minutes:g}m unsynced window"
        report["sync_progress"] = {
            "active": True,
            "baseline_available": False,
            "window_minutes": stall_minutes,
            "detail": detail,
        }
        report["checks"].append(Check("sync_progress", True, detail).as_dict())
        recalculate_report_health(report, config)
        return

    baseline_at, baseline = candidates[-1]
    current_values = {
        "daa": numeric(grpc_metrics.get("virtual_daa_score")),
        "block": numeric(grpc_metrics.get("block_count")),
        "header": numeric(grpc_metrics.get("header_count")),
    }
    baseline_values = {
        "daa": numeric(baseline.get("virtual_daa_score")),
        "block": numeric(baseline.get("block_count")),
        "header": numeric(baseline.get("header_count")),
    }
    deltas = {
        key: (
            None
            if current_values[key] is None or baseline_values[key] is None
            else current_values[key] - baseline_values[key]
        )
        for key in current_values
    }
    ok = any(
        delta is not None and delta >= minimum
        for delta, minimum in (
            (deltas["daa"], float(thresholds.get("min_sync_daa_delta", 1))),
            (deltas["block"], float(thresholds.get("min_sync_block_delta", 1))),
            (deltas["header"], float(thresholds.get("min_sync_header_delta", 1))),
        )
    )
    elapsed_minutes = max(0.0, (checked_at - baseline_at).total_seconds() / 60)
    elapsed_hours = elapsed_minutes / 60
    report["sync_progress"] = {
        "active": True,
        "baseline_available": True,
        "baseline_checked_at": baseline_at.isoformat(),
        "elapsed_minutes": round(elapsed_minutes, 2),
        "daa_delta": deltas["daa"],
        "block_delta": deltas["block"],
        "header_delta": deltas["header"],
        "daa_rate_per_hour": None if deltas["daa"] is None or elapsed_hours <= 0 else deltas["daa"] / elapsed_hours,
        "block_rate_per_hour": None if deltas["block"] is None or elapsed_hours <= 0 else deltas["block"] / elapsed_hours,
        "header_rate_per_hour": None if deltas["header"] is None or elapsed_hours <= 0 else deltas["header"] / elapsed_hours,
    }
    detail = (
        f"daa_delta={format_delta(deltas['daa'])} "
        f"block_delta={format_delta(deltas['block'])} "
        f"header_delta={format_delta(deltas['header'])} "
        f"over {elapsed_minutes:.1f}m"
    )
    report["sync_progress"]["detail"] = detail
    report["checks"].append(Check("sync_progress", ok, detail).as_dict())
    recalculate_report_health(report, config)


def should_emit_alert(
    state: dict[str, Any],
    report: dict[str, Any],
    repeat_minutes: float,
) -> bool:
    previous_status = state.get("status")
    previous_severity = state.get("severity")
    if previous_status != report["status"] or previous_severity != report["severity"]:
        return True
    if report["status"] == "ok":
        return False

    last_alert_at = parse_iso_datetime(state.get("last_alert_at"))
    checked_at = parse_iso_datetime(report.get("checked_at")) or dt.datetime.now().astimezone()
    if last_alert_at is None:
        return True
    elapsed = (checked_at - last_alert_at).total_seconds()
    return elapsed >= repeat_minutes * 60


def recovery_plan(config: dict, severity: str) -> dict[str, Any]:
    recovery = config.get("recovery", {})
    mode = recovery.get("mode", "manual")
    restart_command = recovery.get("restart_command") or []
    return {
        "mode": mode,
        "restart_command_configured": bool(restart_command),
        "action": "none" if severity == "ok" else "manual_approval_required",
        "restart_command": restart_command if mode == "manual" else [],
    }


def html_row(cells: list[Any], tag: str = "td") -> str:
    return "<tr>" + "".join(f"<{tag}>{html.escape(str(cell))}</{tag}>" for cell in cells) + "</tr>"


def css_percent(value: float | int | None) -> str:
    if value is None:
        return "0%"
    return f"{max(0.0, min(100.0, float(value))):.1f}%"


def compact_number(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    magnitude = abs(parsed)
    if magnitude >= 1_000_000_000:
        return f"{parsed / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"{parsed / 1_000_000:.2f}M"
    if magnitude >= 1_000:
        return f"{parsed / 1_000:.1f}K"
    return str(int(parsed)) if parsed.is_integer() else f"{parsed:.2f}"


def sparkline_svg(items: list[dict[str, Any]], key: str, color: str) -> str:
    values = [numeric(item.get(key)) for item in items]
    points = [value for value in values if value is not None]
    if len(points) < 2:
        return '<div class="empty-chart">Not enough history</div>'
    width = 320
    height = 92
    pad = 10
    low = min(points)
    high = max(points)
    span = high - low or 1
    step = (width - pad * 2) / (len(points) - 1)
    coords = []
    for index, value in enumerate(points):
        x = pad + step * index
        y = height - pad - ((value - low) / span) * (height - pad * 2)
        coords.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{pad},{height - pad} {line} {width - pad},{height - pad}"
    return f"""<svg class="sparkline" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(key)} trend">
  <polyline class="spark-area" points="{area}" fill="{html.escape(color)}" opacity="0.12"></polyline>
  <polyline class="spark-line" points="{line}" fill="none" stroke="{html.escape(color)}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
</svg>"""


def mempool_10s_chart(items: list[dict[str, Any]]) -> str:
    buckets: dict[dt.datetime, dict[str, Any]] = {}
    for item in items:
        value = numeric(item.get("mempool_size"))
        checked_at = parse_iso_datetime(str(item.get("checked_at") or ""))
        if value is None or checked_at is None:
            continue
        bucket_at = checked_at.replace(
            second=(checked_at.second // 10) * 10,
            microsecond=0,
        )
        bucket = buckets.setdefault(
            bucket_at,
            {
                "timestamp": bucket_at,
                "value": value,
            },
        )
        bucket["value"] = value

    points = [buckets[key] for key in sorted(buckets)]
    if not points:
        return '<div class="empty-chart">No mempool history</div>'

    width = 720
    height = 164
    left_pad = 32
    right_pad = 66
    top_pad = 14
    bottom_pad = 30
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad
    high = max(float(item["value"]) for item in points) or 1
    step = chart_width / len(points)
    bar_width = max(4, min(14, step * 0.58))

    grid_parts = []
    for ratio in [0, 0.5, 1]:
        line_y = top_pad + chart_height * ratio
        value = high * (1 - ratio)
        grid_parts.append(
            f'<line x1="{left_pad}" x2="{width - right_pad}" y1="{line_y:.1f}" y2="{line_y:.1f}" '
            'stroke="#d9e1e8" stroke-width="1"></line>'
        )
        grid_parts.append(
            f'<text x="{width - right_pad + 10}" y="{line_y + 4:.1f}" fill="#66727f" '
            f'font-size="11" font-weight="700">{html.escape(compact_number(value))}</text>'
        )

    bars = []
    for index, item in enumerate(points):
        value = float(item["value"])
        bar_height = max(2, (value / high) * chart_height)
        x = left_pad + step * index + (step - bar_width) / 2
        y = top_pad + chart_height - bar_height
        label = item["timestamp"].strftime("%H:%M:%S")
        bars.append(
            f'<rect class="mempool-bar" data-bucket="10s" x="{x:.1f}" y="{y:.1f}" '
            f'width="{bar_width:.1f}" height="{bar_height:.1f}" rx="2" fill="#147d64" opacity="0.86">'
            f'<title>{html.escape(label)} 10s mempool size {compact_number(value)}</title>'
            '</rect>'
        )

    label_indexes = sorted({0, len(points) // 2, len(points) - 1})
    labels = []
    for index in label_indexes:
        item = points[index]
        x = left_pad + step * index + step / 2
        anchor = "start" if index == 0 else "end" if index == len(points) - 1 else "middle"
        labels.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}" fill="#66727f" '
            f'font-size="10" font-weight="700">{html.escape(item["timestamp"].strftime("%H:%M:%S"))}</text>'
        )

    return (
        '<svg class="processed-chart mempool-bars" viewBox="0 0 720 164" role="img" '
        'aria-label="Recent mempool size by 10 second bucket">'
        + "".join(grid_parts)
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )


def severity_timeline(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<div class="empty-chart">No status history</div>'
    recent = items[-48:]
    segments = []
    for item in recent:
        severity = str(item.get("severity") or "unknown")
        if severity not in {"ok", "warn", "critical"}:
            severity = "unknown"
        checked_at = html.escape(str(item.get("checked_at") or "unknown"))
        segments.append(
            f'<span class="severity-segment {html.escape(severity)}" title="{checked_at} {html.escape(severity)}"></span>'
        )
    return '<div class="severity-timeline" aria-label="Recent severity timeline">' + "".join(segments) + "</div>"


def processed_rate_chart(samples: list[dict[str, Any]]) -> str:
    points = [
        {
            "timestamp": str(item.get("timestamp") or ""),
            "rate": numeric(item.get("blocks_per_second")),
        }
        for item in samples
    ]
    points = [item for item in points if item["rate"] is not None]
    if not points:
        return '<div class="empty-chart">No processed block stats</div>'

    width = 720
    height = 164
    left_pad = 32
    right_pad = 66
    top_pad = 14
    bottom_pad = 30
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad
    high = max(float(item["rate"]) for item in points) or 1
    step = chart_width / len(points)
    bar_width = max(4, min(14, step * 0.58))

    grid_parts = []
    for ratio in [0, 0.5, 1]:
        line_y = top_pad + chart_height * ratio
        value = high * (1 - ratio)
        grid_parts.append(
            f'<line x1="{left_pad}" x2="{width - right_pad}" y1="{line_y:.1f}" y2="{line_y:.1f}" '
            'stroke="#d9e1e8" stroke-width="1"></line>'
        )
        grid_parts.append(
            f'<text x="{width - right_pad + 10}" y="{line_y + 4:.1f}" fill="#66727f" '
            f'font-size="11" font-weight="700">{html.escape(f"{value:.1f}/s")}</text>'
        )

    bars = []
    for index, item in enumerate(points):
        rate = float(item["rate"])
        bar_height = max(2, (rate / high) * chart_height)
        x = left_pad + step * index + (step - bar_width) / 2
        y = top_pad + chart_height - bar_height
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" '
            f'rx="2" fill="#147a46" opacity="0.88">'
            f'<title>{html.escape(item["timestamp"])} {rate:.2f} blocks/s</title>'
            '</rect>'
        )

    labels = []
    label_indexes = sorted({0, len(points) // 2, len(points) - 1})
    for index in label_indexes:
        item = points[index]
        parsed = parse_iso_datetime(item["timestamp"])
        label = parsed.strftime("%m-%d %H:%M:%S") if parsed else item["timestamp"][-14:]
        x = left_pad + step * index + step / 2
        anchor = "start" if index == 0 else "end" if index == len(points) - 1 else "middle"
        labels.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}" fill="#66727f" '
            f'font-size="10" font-weight="700">{html.escape(label)}</text>'
        )

    return (
        '<svg class="processed-chart" viewBox="0 0 720 164" role="img" '
        'aria-label="Recent processed blocks per second">'
        + "".join(grid_parts)
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )


def transaction_rate_chart(samples: list[dict[str, Any]]) -> str:
    points = [
        {
            "timestamp": str(item.get("timestamp") or ""),
            "rate": numeric(item.get("transactions_per_second")),
        }
        for item in samples
    ]
    points = [item for item in points if item["rate"] is not None]
    if not points:
        return '<div class="empty-chart">No transaction stats</div>'

    width = 720
    height = 164
    left_pad = 32
    right_pad = 66
    top_pad = 14
    bottom_pad = 30
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad
    high = max(float(item["rate"]) for item in points) or 1
    step = chart_width / len(points)
    bar_width = max(4, min(14, step * 0.58))

    grid_parts = []
    for ratio in [0, 0.5, 1]:
        line_y = top_pad + chart_height * ratio
        value = high * (1 - ratio)
        grid_parts.append(
            f'<line x1="{left_pad}" x2="{width - right_pad}" y1="{line_y:.1f}" y2="{line_y:.1f}" '
            'stroke="#d9e1e8" stroke-width="1"></line>'
        )
        grid_parts.append(
            f'<text x="{width - right_pad + 10}" y="{line_y + 4:.1f}" fill="#66727f" '
            f'font-size="11" font-weight="700">{html.escape(f"{value:.1f}/s")}</text>'
        )

    bars = []
    for index, item in enumerate(points):
        rate = float(item["rate"])
        bar_height = max(2, (rate / high) * chart_height)
        x = left_pad + step * index + (step - bar_width) / 2
        y = top_pad + chart_height - bar_height
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" '
            f'rx="2" fill="#a86400" opacity="0.86">'
            f'<title>{html.escape(item["timestamp"])} {rate:.2f} tx/s</title>'
            '</rect>'
        )

    labels = []
    label_indexes = sorted({0, len(points) // 2, len(points) - 1})
    for index in label_indexes:
        item = points[index]
        parsed = parse_iso_datetime(item["timestamp"])
        label = parsed.strftime("%m-%d %H:%M:%S") if parsed else item["timestamp"][-14:]
        x = left_pad + step * index + step / 2
        anchor = "start" if index == 0 else "end" if index == len(points) - 1 else "middle"
        labels.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}" fill="#66727f" '
            f'font-size="10" font-weight="700">{html.escape(label)}</text>'
        )

    return (
        '<svg class="processed-chart" viewBox="0 0 720 164" role="img" '
        'aria-label="Recent transactions per second">'
        + "".join(grid_parts)
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )


def relay_intake_chart(samples: list[dict[str, Any]]) -> str:
    points = [
        {
            "timestamp": str(item.get("timestamp") or ""),
            "blocks": numeric(item.get("blocks")),
        }
        for item in samples
    ]
    points = [item for item in points if item["blocks"] is not None]
    if not points:
        return '<div class="empty-chart">No relay events</div>'

    width = 720
    height = 164
    left_pad = 32
    right_pad = 62
    top_pad = 14
    bottom_pad = 30
    chart_width = width - left_pad - right_pad
    chart_height = height - top_pad - bottom_pad
    high = max(float(item["blocks"]) for item in points) or 1
    step = chart_width / len(points)
    bar_width = max(3, min(12, step * 0.58))

    grid_parts = []
    for ratio in [0, 0.5, 1]:
        line_y = top_pad + chart_height * ratio
        value = high * (1 - ratio)
        grid_parts.append(
            f'<line x1="{left_pad}" x2="{width - right_pad}" y1="{line_y:.1f}" y2="{line_y:.1f}" '
            'stroke="#d9e1e8" stroke-width="1"></line>'
        )
        grid_parts.append(
            f'<text x="{width - right_pad + 10}" y="{line_y + 4:.1f}" fill="#66727f" '
            f'font-size="11" font-weight="700">{html.escape(f"{value:.0f}")}</text>'
        )

    bars = []
    for index, item in enumerate(points):
        blocks = float(item["blocks"])
        bar_height = max(2, (blocks / high) * chart_height)
        x = left_pad + step * index + (step - bar_width) / 2
        y = top_pad + chart_height - bar_height
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" '
            f'rx="2" fill="#276b74" opacity="0.86">'
            f'<title>{html.escape(item["timestamp"])} {blocks:.0f} relay blocks</title>'
            '</rect>'
        )

    labels = []
    label_indexes = sorted({0, len(points) // 2, len(points) - 1})
    for index in label_indexes:
        item = points[index]
        parsed = parse_iso_datetime(item["timestamp"])
        label = parsed.strftime("%m-%d %H:%M:%S") if parsed else item["timestamp"][-14:]
        x = left_pad + step * index + step / 2
        anchor = "start" if index == 0 else "end" if index == len(points) - 1 else "middle"
        labels.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}" fill="#66727f" '
            f'font-size="10" font-weight="700">{html.escape(label)}</text>'
        )

    return (
        '<svg class="processed-chart" viewBox="0 0 720 164" role="img" '
        'aria-label="Recent relay accepted blocks">'
        + "".join(grid_parts)
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )


def visual_card(label: str, value: Any, detail: str = "", tone: str = "neutral") -> str:
    return f"""<section class="v-card {html.escape(tone)}">
  <div class="v-label">{html.escape(str(label))}</div>
  <div class="v-value">{html.escape(str(value))}</div>
  <div class="v-detail">{html.escape(str(detail))}</div>
</section>"""


def check_pill(check: dict[str, Any]) -> str:
    state = "ok" if check.get("ok") else "fail"
    label = "OK" if check.get("ok") else "FAIL"
    return f"""<div class="check-pill {state}">
  <span>{html.escape(label)}</span>
  <strong>{html.escape(str(check.get("name", "unknown")))}</strong>
</div>"""


def triage_queue(checks: list[dict[str, Any]]) -> str:
    failed = [check for check in checks if not check.get("ok")]
    if not failed:
        return """<div class="triage-empty">
  <strong>No active triage items</strong>
  <span>All checks passed in the latest watchtower run.</span>
</div>"""

    action_by_check = {
        "process": "Confirm kaspad is running before checking network state.",
        "data_dir": "Verify the configured kaspad data directory is mounted.",
        "rpc_tcp": "Check local RPC listener and firewall exposure.",
        "grpc_metrics": "Inspect gRPC endpoint reachability and protobuf compatibility.",
        "sync_status": "Confirm sync progress before considering recovery.",
        "peer_count": "Check peer connectivity and network reachability.",
        "block_progress": "Compare relay freshness with sync and peer state.",
        "processed_stats_freshness": "Inspect kaspad processed-stats log output and transaction throughput freshness.",
        "disk_free": "Free disk space or move data before restart attempts.",
        "log_file": "Verify the configured kaspad log path is readable.",
    }
    items = []
    for check in failed:
        name = str(check.get("name", "unknown"))
        detail = str(check.get("detail", "No detail provided"))
        tone = "critical" if name in CRITICAL_CHECKS else "warn"
        action = action_by_check.get(name, "Review detail and compare with recent history.")
        items.append(
            f"""<article class="triage-card {html.escape(tone)}">
  <div class="triage-top">
    <span>{html.escape(tone.upper())}</span>
    <strong>{html.escape(name)}</strong>
  </div>
  <p>{html.escape(detail)}</p>
  <div class="triage-action">{html.escape(action)}</div>
</article>"""
        )
    return '<div class="triage-list">' + "\n".join(items) + "</div>"


def command_center(severity: str) -> str:
    commands = [
        ("Summary", "make summary", "Concise status for chat or quick operator review."),
        ("Incident", "make incident-report", "Sanitized Markdown report for escalation."),
        ("Diagnostics", "make diagnostics-archive", "Collect a local diagnostics bundle."),
    ]
    if severity == "ok":
        commands.append(("Smoke", "make smoke", "Run the local release-readiness smoke suite."))
    else:
        commands.append(("Recovery Dry-Run", "make recover-dry-run", "Review recovery decision before any approved restart."))
    cards = []
    for label, command, detail in commands:
        cards.append(
            f"""<article class="command-card">
  <div class="command-top">
    <div class="command-label">{html.escape(label)}</div>
    <button type="button" class="command-copy" data-copy="{html.escape(command)}">Copy</button>
  </div>
  <code>{html.escape(command)}</code>
  <p>{html.escape(detail)}</p>
</article>"""
        )
    return '<div class="command-grid">' + "\n".join(cards) + "</div>"


def check_passed(report: dict[str, Any], name: str) -> bool:
    for check in report.get("checks", []):
        if check.get("name") == name:
            return bool(check.get("ok"))
    return True


def tone_for_check(report: dict[str, Any], name: str) -> str:
    if check_passed(report, name):
        return "ok"
    return "critical" if name in CRITICAL_CHECKS else "warn"


def recent_recovery_records(config: dict, limit: int = 5) -> list[dict[str, Any]]:
    try:
        return load_jsonl(recovery_history_path(config))[-limit:]
    except (OSError, json.JSONDecodeError):
        return []


def write_status_page(
    path: Path,
    report: dict[str, Any],
    state: dict[str, Any],
    benchmark_path: Path | None = None,
    recovery_records: list[dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checks = "\n".join(
        html_row([
            "OK" if check["ok"] else "FAIL",
            check["name"],
            check["detail"],
        ])
        for check in report["checks"]
    )
    history = "\n".join(
        html_row([
            item.get("checked_at", ""),
            item.get("severity", ""),
            ",".join(item.get("failed_checks") or []),
            item.get("peer_count", ""),
            item.get("relay_blocks_in_window", ""),
            item.get("virtual_daa_score", ""),
        ])
        for item in reversed(state.get("history", [])[-30:])
    )
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
    sync_progress = report.get("sync_progress") or {}
    recovery = report.get("recovery") or {}
    failed = failed_check_names(report)
    failure_text = ", ".join(failed) if failed else "None"
    last_alert_at = state.get("last_alert_at") or "None"
    benchmark_summary = build_benchmark_summary(
        benchmark_path or Path(DEFAULT_CONFIG["benchmark_path"]),
        limit=48,
    )
    benchmark_rows = "\n".join(
        html_row([label, value])
        for label, value in [
            ("Snapshots", benchmark_summary["snapshots"]),
            ("Window", benchmark_summary["window"]),
            ("DAA Rate", benchmark_summary["daa_rate"]),
            ("Block Rate", benchmark_summary["block_rate"]),
            ("Relay Average", benchmark_summary["relay_rate"]),
            ("Latest Peer State", benchmark_summary["latest_peer_state"]),
            ("Severity Counts", benchmark_summary["severity_counts"]),
            ("OK Ratio", format_ratio(benchmark_summary.get("ok_ratio"))),
            ("Minimum Peers", format_optional_number(benchmark_summary.get("min_peer_count"))),
            ("Minimum Disk Free", format_gib(benchmark_summary.get("min_disk_free_gb"))),
            ("Disk Free Delta", benchmark_summary["disk_delta"]),
        ]
    )
    recovery_rows = "\n".join(
        html_row(
            [
                item.get("started_at", ""),
                item.get("action", ""),
                item.get("severity_before", ""),
                item.get("severity_after", ""),
                ",".join(item.get("failed_checks_before") or []),
                item.get("reason", ""),
            ]
        )
        for item in reversed(recovery_records or [])
    )
    if not recovery_rows:
        recovery_rows = html_row(["No recovery attempts recorded", "", "", "", "", ""])
    disk = report.get("disk") or {}
    checks_total = len(report["checks"])
    checks_ok = sum(1 for check in report["checks"] if check.get("ok"))
    checks_percent = (checks_ok / checks_total * 100) if checks_total else 0
    ok_ratio = numeric(benchmark_summary.get("ok_ratio"))
    ok_percent = (ok_ratio or 0) * 100
    history_items = state.get("history", [])[-60:]
    relay_chart = sparkline_svg(history_items, "relay_blocks_in_window", "#147d64")
    daa_chart = sparkline_svg(history_items, "virtual_daa_score", "#3858e9")
    peer_chart = sparkline_svg(history_items, "peer_count", "#b26a00")
    mempool_history = history_items + [
        {
            "checked_at": report.get("checked_at"),
            "mempool_size": grpc_metrics.get("mempool_size"),
        }
    ]
    mempool_chart = mempool_10s_chart(mempool_history)
    hashrate_history = history_items + [{"network_hashes_per_second": grpc_metrics.get("network_hashes_per_second")}]
    if sum(1 for item in hashrate_history if numeric(item.get("network_hashes_per_second")) is not None) == 1:
        hashrate_history.append({"network_hashes_per_second": grpc_metrics.get("network_hashes_per_second")})
    hashrate_chart = sparkline_svg(hashrate_history, "network_hashes_per_second", "#5b6c00")
    severity_chart = severity_timeline(history_items)
    latest_processed = progress.get("latest_processed") or {}
    processed_chart = processed_rate_chart(progress.get("processed_samples") or [])
    processed_rate = latest_processed.get("blocks_per_second")
    processed_rate_text = "unknown" if processed_rate is None else f"{float(processed_rate):.1f}/s"
    transaction_chart = transaction_rate_chart(progress.get("processed_samples") or [])
    transaction_rate = latest_processed.get("transactions_per_second")
    transaction_rate_text = "unknown" if transaction_rate is None else f"{float(transaction_rate):.1f}/s"
    mempool_detail = "10-second buckets from status history"
    latest_processed_age = progress.get("latest_processed_age_seconds")
    latest_processed_age_text = "unknown" if latest_processed_age is None else f"{latest_processed_age}s old"
    processed_detail = (
        "No recent processed stats"
        if not latest_processed
        else (
            f"{latest_processed.get('blocks', 'unknown')} blocks / "
            f"{latest_processed.get('seconds', 'unknown')}s"
        )
    )
    transaction_detail = (
        "No recent transaction stats"
        if not latest_processed
        else (
            f"{latest_processed.get('transactions', 'unknown')} tx / "
            f"{latest_processed.get('seconds', 'unknown')}s · "
            f"{latest_processed_age_text}"
        )
    )
    relay_samples = progress.get("relay_samples") or []
    relay_intake = relay_intake_chart(relay_samples)
    relay_detail = (
        "No recent relay events"
        if not relay_samples
        else (
            f"{progress.get('relay_blocks_in_window', 0)} blocks / "
            f"{progress.get('relay_events_in_window', 0)} events in "
            f"{progress.get('window_minutes', 'unknown')}m"
        )
    )
    check_pills = "\n".join(check_pill(check) for check in report["checks"])
    triage_items = triage_queue(report["checks"])
    severity = str(report.get("severity", "unknown"))
    commands = command_center(severity)
    latest_relay_age = progress.get("latest_relay_age_seconds")
    latest_relay_text = "unknown" if latest_relay_age is None else f"{latest_relay_age}s"
    next_action = "No immediate action"
    if severity == "critical":
        next_action = "Review failed checks and run recovery dry-run before approved restart"
    elif severity == "warn":
        next_action = "Inspect warning checks and confirm trend before recovery"
    network_text = grpc_metrics.get("network_id", "unknown")
    sync_text = grpc_metrics.get("is_synced", "unknown")
    visual_cards = "\n".join(
        [
            visual_card("Peers", grpc_metrics.get("peer_count", "unknown"), f"active {grpc_metrics.get('active_peers', 'unknown')}", tone_for_check(report, "peer_count")),
            visual_card("Relay", latest_relay_text, f"{compact_number(progress.get('relay_blocks_in_window'))} blocks / {progress.get('window_minutes', 'unknown')}m", tone_for_check(report, "block_progress")),
            visual_card("Sync", "synced" if sync_text is True else str(sync_text), f"network {network_text}", tone_for_check(report, "sync_status")),
            visual_card("Tx Rate", transaction_rate_text, transaction_detail, tone_for_check(report, "processed_stats_freshness")),
            visual_card("DAA Score", compact_number(grpc_metrics.get("virtual_daa_score")), f"tips {grpc_metrics.get('tip_count', 'unknown')}", "neutral"),
            visual_card("Hashrate", format_hashrate(grpc_metrics.get("network_hashes_per_second")), f"window {grpc_metrics.get('network_hashrate_window_size', 'unknown')}", "neutral"),
            visual_card("Disk Free", format_gib(disk.get("free_gb")), f"{disk.get('free_percent', 'unknown')}% free", tone_for_check(report, "disk_free")),
            visual_card("Benchmark OK", format_ratio(ok_ratio), f"{benchmark_summary.get('snapshots')} snapshots", "ok" if (ok_ratio or 0) >= 0.95 else "warn"),
        ]
    )
    incident_panel = f"""
    <section class="incident {html.escape(severity)}">
      <div>
        <div class="eyebrow">Operator verdict</div>
        <h2>{html.escape(severity.upper())}</h2>
        <p>{html.escape(next_action)}</p>
      </div>
      <div class="incident-facts">
        <div><span>Failed</span><strong>{html.escape(failure_text)}</strong></div>
        <div><span>Network</span><strong>{html.escape(str(network_text))}</strong></div>
        <div><span>Recovery</span><strong>{html.escape(str(recovery.get('action', 'unknown')))}</strong></div>
      </div>
    </section>
    """
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>Kaspa Node Watchtower</title>
  <style>
    :root {{
      --ink: #1f2933;
      --muted: #66727f;
      --line: #d9e1e8;
      --panel: #ffffff;
      --page: #f5f7f8;
      --ok: #147a46;
      --ok-soft: #e8f5ee;
      --warn: #a86400;
      --warn-soft: #fff3d9;
      --critical: #b42318;
      --critical-soft: #fdecea;
      --neutral-soft: #f7f9fb;
      --accent: #276b74;
      --accent-soft: #e7f3f4;
      --shadow: 0 14px 34px rgba(31, 41, 51, 0.07);
    }}
    * {{ box-sizing: border-box; }}
    html {{ overflow-x: hidden; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
      overflow-x: hidden;
    }}
    main {{ width: 100%; max-width: 1180px; min-width: 0; margin: 0 auto; padding: 22px; }}
    .hero {{
      background: linear-gradient(180deg, #ffffff 0%, #fbfcfd 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 14px;
      box-shadow: var(--shadow);
    }}
    .hero-top {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 24px; margin: 0 0 6px; line-height: 1.2; }}
    h2 {{ font-size: 16px; margin: 0 0 12px; }}
    .subtle {{ color: var(--muted); font-size: 13px; max-width: 100%; overflow-wrap: anywhere; word-break: break-word; }}
    .badge {{
      display: inline-flex;
      min-width: 92px;
      justify-content: center;
      padding: 7px 10px;
      border-radius: 999px;
      color: #fff;
      font-weight: 700;
      text-transform: uppercase;
      font-size: 13px;
    }}
    .badge.ok {{ background: var(--ok); }}
    .badge.warn {{ background: var(--warn); }}
    .badge.critical {{ background: var(--critical); }}
    .eyebrow {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .incident {{
      display: grid;
      grid-template-columns: 1fr minmax(300px, 0.9fr);
      gap: 14px;
      align-items: stretch;
      border: 1px solid var(--line);
      border-left: 6px solid var(--ok);
      background: var(--panel);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
      box-shadow: var(--shadow);
    }}
    .incident.warn {{ border-left-color: var(--warn); background: linear-gradient(90deg, var(--warn-soft), #ffffff 42%); }}
    .incident.critical {{ border-left-color: var(--critical); background: linear-gradient(90deg, var(--critical-soft), #ffffff 42%); }}
    .incident h2 {{ font-size: 28px; margin: 0 0 6px; }}
    .incident p {{ margin: 0; color: var(--muted); line-height: 1.45; }}
    .incident-facts {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .incident-facts div {{
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      min-width: 0;
    }}
    .incident-facts span {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .incident-facts strong {{ display: block; overflow-wrap: anywhere; font-size: 13px; }}
    .hero-strip {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .bar-block {{
      display: grid;
      gap: 7px;
    }}
    .bar-meta {{
      display: flex;
      justify-content: space-between;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .bar {{
      height: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: #e7ecef;
    }}
    .bar-fill {{
      height: 100%;
      width: var(--fill);
      background: var(--accent);
      border-radius: inherit;
    }}
    .bar-fill.ok {{ background: var(--ok); }}
    .bar-fill.warn {{ background: var(--warn); }}
    .visual-grid {{
      display: grid;
      grid-template-columns: repeat(8, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .market-watch {{
      display: grid;
      grid-template-columns: 300px 1fr;
      gap: 14px;
      align-items: stretch;
      margin-bottom: 14px;
    }}
    .market-price {{
      display: grid;
      gap: 9px;
      align-content: start;
    }}
    .market-pair {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }}
    .market-source {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
    }}
    .market-last {{ font-size: 34px; font-weight: 900; line-height: 1; overflow-wrap: anywhere; }}
    .market-change {{ font-size: 14px; font-weight: 800; }}
    .market-change.up {{ color: var(--ok); }}
    .market-change.down {{ color: var(--critical); }}
    .market-meta {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }}
    .market-signal-watch {{
      display: grid;
      gap: 7px;
    }}
    .market-signal-list {{
      display: grid;
      gap: 6px;
    }}
    .market-signal-row {{
      display: grid;
      grid-template-columns: 38px 1fr;
      gap: 8px;
      align-items: center;
      min-height: 28px;
      padding: 6px 8px;
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #f8fafc;
      font-size: 12px;
      font-weight: 800;
    }}
    .market-signal-row span:first-child {{ color: var(--muted); }}
    .market-signal-row.up {{ background: var(--ok-soft); border-color: #b9e3ca; color: var(--ok); }}
    .market-signal-row.down {{ background: var(--critical-soft); border-color: #f1b8b2; color: var(--critical); }}
    .market-signal-row.warn {{ background: #fff7ed; border-color: #fed7aa; color: #b26a00; }}
    .market-signal-row.cool {{ background: #ecfeff; border-color: #a5f3fc; color: #276b74; }}
    .market-chart-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .market-title-row {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      min-width: 0;
    }}
    .market-trend-badge,
    .market-rsi-badge {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 3px 8px;
      border: 1px solid #d9e1e8;
      border-radius: 999px;
      background: #f8fafc;
      color: var(--muted);
      font-size: 11px;
      font-weight: 900;
      line-height: 1;
      white-space: nowrap;
    }}
    .market-trend-badge.up {{ background: var(--ok-soft); border-color: #b9e3ca; color: var(--ok); }}
    .market-trend-badge.down {{ background: var(--critical-soft); border-color: #f1b8b2; color: var(--critical); }}
    .market-trend-badge.neutral {{ background: #fff7ed; border-color: #fed7aa; color: #b26a00; }}
    .market-rsi-badge.hot {{ background: #fff7ed; border-color: #fed7aa; color: #b26a00; }}
    .market-rsi-badge.cool {{ background: #ecfeff; border-color: #a5f3fc; color: #276b74; }}
    .market-rsi-badge.neutral {{ background: #f8fafc; border-color: #d9e1e8; color: var(--muted); }}
    .market-chart {{
      width: 100%;
      height: 230px;
      display: block;
      background: #f8fafc;
      border: 1px solid #edf1f6;
      border-radius: 8px;
    }}
    .market-timeframe-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-bottom: 14px;
    }}
    .market-watch > .panel,
    .market-timeframe-grid > .panel,
    .market-cross-panel {{
      min-height: 322px;
    }}
    .market-watch > .panel:not(.market-price),
    .market-timeframe-grid > .panel,
    .market-cross-panel {{
      display: flex;
      flex-direction: column;
    }}
    .market-watch > .panel:not(.market-price) .market-chart,
    .market-timeframe-grid .market-chart,
    .market-cross-panel .market-chart {{
      flex: 1 1 auto;
      min-height: 230px;
    }}
    .market-legend {{
      display: flex;
      gap: 12px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      margin-bottom: 8px;
    }}
    .market-legend span {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .market-legend i {{
      width: 18px;
      height: 3px;
      border-radius: 999px;
      display: inline-block;
      background: currentColor;
    }}
    .market-volume-panel {{
      min-height: 340px;
      margin-bottom: 14px;
      display: flex;
      flex-direction: column;
    }}
    .market-volume-panel .market-chart {{
      flex: 1 1 auto;
      min-height: 244px;
    }}
    .market-volume-panel .market-legend {{
      flex-wrap: wrap;
      row-gap: 8px;
    }}
    .market-volume-panel .market-legend i {{
      width: 10px;
      height: 10px;
      border-radius: 3px;
    }}
    .market-volume-panel .market-legend .total i {{
      width: 18px;
      height: 3px;
      border-radius: 999px;
    }}
    .futures-panel {{
      margin-bottom: 14px;
    }}
    .futures-panel .market-meta {{
      grid-template-columns: repeat(6, minmax(0, 1fr));
    }}
    .futures-trend-panel {{
      min-height: 340px;
      margin-bottom: 14px;
      display: flex;
      flex-direction: column;
    }}
    .futures-trend-panel .market-chart {{
      flex: 1 1 auto;
      min-height: 244px;
    }}
    .liquidation-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-bottom: 14px;
    }}
    .liquidation-grid > .panel {{
      min-height: 332px;
      display: flex;
      flex-direction: column;
    }}
    .liquidation-grid .market-chart {{
      flex: 1 1 auto;
      min-height: 244px;
    }}
    .market-status {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      min-width: 0;
      overflow-wrap: anywhere;
      text-align: right;
    }}
    .v-card, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-width: 0;
      box-shadow: 0 8px 20px rgba(31, 41, 51, 0.04);
    }}
    .v-card.ok {{ background: var(--ok-soft); border-color: #b9e3ca; }}
    .v-card.warn {{ background: var(--warn-soft); border-color: #f4d493; }}
    .v-card.critical {{ background: var(--critical-soft); border-color: #f1b8b2; }}
    .v-label {{ color: var(--muted); font-size: 12px; margin-bottom: 5px; font-weight: 700; }}
    .v-value {{ font-size: 21px; font-weight: 800; line-height: 1.1; overflow-wrap: anywhere; }}
    .v-detail {{ color: var(--muted); font-size: 12px; margin-top: 5px; min-height: 16px; }}
    .layout {{ display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 14px; margin-bottom: 14px; }}
    .chart-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 14px; }}
    .chart-head {{ display: flex; justify-content: space-between; gap: 10px; margin-bottom: 8px; min-width: 0; }}
    .chart-value {{ font-size: 18px; font-weight: 800; overflow-wrap: anywhere; text-align: right; }}
    .sparkline {{ width: 100%; height: 92px; display: block; }}
    .processed-chart {{ width: 100%; height: 164px; display: block; background: #f8fafc; border-radius: 8px; }}
    .empty-chart {{ height: 92px; display: grid; place-items: center; color: var(--muted); font-size: 13px; background: #f8fafc; border-radius: 8px; }}
    .severity-timeline {{
      height: 92px;
      display: grid;
      grid-template-columns: repeat(48, minmax(2px, 1fr));
      gap: 3px;
      align-items: end;
      padding: 10px;
      background: #f8fafc;
      border-radius: 8px;
    }}
    .severity-segment {{
      display: block;
      height: 100%;
      min-height: 18px;
      border-radius: 999px;
      background: #c9d3dc;
    }}
    .severity-segment.ok {{ background: var(--ok); }}
    .severity-segment.warn {{ background: var(--warn); }}
    .severity-segment.critical {{ background: var(--critical); }}
    .check-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; }}
    .triage-list {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
    }}
    .triage-card {{
      border: 1px solid var(--line);
      border-left: 5px solid var(--warn);
      border-radius: 8px;
      background: #ffffff;
      padding: 11px;
      min-width: 0;
    }}
    .triage-card.critical {{ border-left-color: var(--critical); background: var(--critical-soft); }}
    .triage-card.warn {{ border-left-color: var(--warn); background: var(--warn-soft); }}
    .triage-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
      min-width: 0;
    }}
    .triage-top span {{
      border-radius: 999px;
      background: rgba(31, 41, 51, 0.82);
      color: #fff;
      font-size: 11px;
      font-weight: 800;
      padding: 3px 7px;
      flex: 0 0 auto;
    }}
    .triage-top strong {{ font-size: 14px; overflow-wrap: anywhere; text-align: right; }}
    .triage-card p {{ margin: 0 0 9px; color: var(--ink); font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; }}
    .triage-action {{ color: var(--muted); font-size: 12px; line-height: 1.35; }}
    .triage-empty {{
      display: grid;
      gap: 4px;
      background: var(--ok-soft);
      border: 1px solid #b9e3ca;
      border-radius: 8px;
      padding: 12px;
    }}
    .triage-empty span {{ color: var(--muted); font-size: 13px; }}
    .command-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
    }}
    .command-card {{
      display: grid;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 11px;
      min-width: 0;
    }}
    .command-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-width: 0;
    }}
    .command-label {{ color: var(--muted); font-size: 12px; font-weight: 800; }}
    .command-copy {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: var(--ink);
      cursor: pointer;
      font-size: 12px;
      font-weight: 800;
      min-height: 30px;
      padding: 5px 9px;
      flex: 0 0 auto;
    }}
    .command-copy:hover {{ border-color: var(--accent); color: var(--accent); }}
    .command-copy.copied {{ background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }}
    .command-card code {{ display: block; font-weight: 800; }}
    .command-card p {{ margin: 0; color: var(--muted); font-size: 12px; line-height: 1.35; }}
    .check-pill {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      min-width: 0;
    }}
    .check-pill span {{
      font-size: 11px;
      font-weight: 800;
      color: #fff;
      border-radius: 999px;
      padding: 3px 7px;
      background: var(--ok);
      flex: 0 0 auto;
    }}
    .check-pill.fail span {{ background: var(--critical); }}
    .check-pill strong {{ font-size: 13px; overflow-wrap: anywhere; }}
    .context-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .context-item {{ background: #f8fafc; border: 1px solid #edf1f6; border-radius: 8px; padding: 10px; min-width: 0; }}
    .context-label {{ color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .context-value {{ font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    code {{ background: #eef2f6; padding: 2px 4px; border-radius: 4px; overflow-wrap: anywhere; word-break: break-all; white-space: normal; }}
    .ok-text {{ color: var(--ok); font-weight: 700; }}
    .fail-text {{ color: var(--critical); font-weight: 700; }}
    main > .panel + .panel {{ margin-top: 14px; }}
    @media (max-width: 760px) {{
      main {{ padding: 14px; }}
      .hero-top, .hero-strip, .layout, .chart-grid, .market-watch, .market-timeframe-grid, .liquidation-grid, .context-grid {{ display: block; }}
      .incident, .incident-facts {{ display: block; }}
      .incident-facts div {{ margin-top: 8px; }}
      .chart-head {{ display: block; }}
      .chart-value {{ text-align: left; margin-bottom: 8px; }}
      .market-chart-head {{ display: block; }}
      .market-title-row {{ margin-bottom: 6px; }}
      .market-status {{ text-align: left; }}
      .visual-grid {{ display: block; }}
      .badge {{ margin-top: 10px; }}
      .v-card, .panel {{ margin-bottom: 12px; }}
      .market-meta {{ grid-template-columns: 1fr; }}
      .futures-panel .market-meta {{ grid-template-columns: 1fr; }}
      .panel {{ overflow-x: auto; }}
      .bar-block, .context-item {{ margin-bottom: 10px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="hero-top">
        <div>
          <h1>Kaspa Node Watchtower</h1>
          <div class="subtle">{html.escape(report['node_name'])} · checked at <code>{html.escape(report['checked_at'])}</code> · auto-refresh 60s</div>
        </div>
        <div class="badge {html.escape(report['severity'])}">{html.escape(report['severity'])}</div>
      </div>
      <div class="hero-strip">
        <div class="bar-block">
          <div class="bar-meta"><span>checks passing</span><span>{checks_ok}/{checks_total}</span></div>
          <div class="bar"><div class="bar-fill ok" style="--fill: {css_percent(checks_percent)}"></div></div>
        </div>
        <div class="bar-block">
          <div class="bar-meta"><span>benchmark OK ratio</span><span>{format_ratio(ok_ratio)}</span></div>
          <div class="bar"><div class="bar-fill" style="--fill: {css_percent(ok_percent)}"></div></div>
        </div>
      </div>
    </section>
    {incident_panel}
    <section class="visual-grid">
      {visual_cards}
    </section>
    <section class="market-watch">
      <section class="panel market-price">
        <div class="market-pair">
          <h2>KAS/USDT</h2>
          <span class="market-source">Bybit spot</span>
        </div>
        <div id="market-last" class="market-last">Loading</div>
        <div id="market-change" class="market-change">24h change pending</div>
        <div class="market-meta">
          <div class="context-item"><div class="context-label">24h high</div><div id="market-high" class="context-value">unknown</div></div>
          <div class="context-item"><div class="context-label">24h low</div><div id="market-low" class="context-value">unknown</div></div>
          <div class="context-item"><div class="context-label">24h volume</div><div id="market-volume" class="context-value">unknown</div></div>
          <div class="context-item"><div class="context-label">Updated</div><div id="market-updated" class="context-value">pending</div></div>
        </div>
        <div class="market-signal-watch">
          <div class="context-label">Signal Watch</div>
          <div id="market-signal-list" class="market-signal-list">
            <div class="market-signal-row"><span>all</span><strong>Waiting for candles</strong></div>
          </div>
        </div>
      </section>
      <section class="panel">
        <div class="market-chart-head">
          <div class="market-title-row">
            <h2>KAS/USDT 15m</h2>
            <span id="market-trend-15m" class="market-trend-badge">Trend pending</span>
            <span id="market-rsi-15m" class="market-rsi-badge">RSI pending</span>
          </div>
          <div id="market-status" class="market-status">Loading candles</div>
        </div>
        <svg id="market-chart" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT 15 minute candlestick chart"></svg>
      </section>
    </section>
    <section class="market-timeframe-grid">
      <section class="panel">
        <div class="market-chart-head">
          <div class="market-title-row">
            <h2>KAS/USDT 4h</h2>
            <span id="market-trend-4h" class="market-trend-badge">Trend pending</span>
            <span id="market-rsi-4h" class="market-rsi-badge">RSI pending</span>
          </div>
          <div id="market-status-4h" class="market-status">Loading candles</div>
        </div>
        <svg id="market-chart-4h" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT 4 hour candlestick chart"></svg>
      </section>
      <section class="panel">
        <div class="market-chart-head">
          <div class="market-title-row">
            <h2>KAS/USDT 1D</h2>
            <span id="market-trend-1d" class="market-trend-badge">Trend pending</span>
            <span id="market-rsi-1d" class="market-rsi-badge">RSI pending</span>
          </div>
          <div id="market-status-1d" class="market-status">Loading candles</div>
        </div>
        <svg id="market-chart-1d" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT daily candlestick chart"></svg>
      </section>
      <section class="panel">
        <div class="market-chart-head">
          <div class="market-title-row">
            <h2>KAS/USDT 1W</h2>
            <span id="market-trend-1w" class="market-trend-badge">Trend pending</span>
            <span id="market-rsi-1w" class="market-rsi-badge">RSI pending</span>
          </div>
          <div id="market-status-1w" class="market-status">Loading candles</div>
        </div>
        <svg id="market-chart-1w" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT weekly candlestick chart"></svg>
      </section>
      <section class="panel">
        <div class="market-chart-head">
          <div class="market-title-row">
            <h2>KAS/USDT 1M</h2>
            <span id="market-trend-1m" class="market-trend-badge">Trend pending</span>
            <span id="market-rsi-1m" class="market-rsi-badge">RSI pending</span>
          </div>
          <div id="market-status-1m" class="market-status">Loading candles</div>
        </div>
        <svg id="market-chart-1m" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT monthly candlestick chart"></svg>
      </section>
    </section>
    <section class="panel market-cross-panel">
      <div class="market-chart-head">
        <h2>KAS/USDT vs BTC/USDT 1D</h2>
        <div id="market-cross-status" class="market-status">Loading daily cross</div>
      </div>
      <div class="market-legend">
        <span style="color: #b42318"><i></i>KAS/USDT</span>
        <span style="color: #2563eb"><i></i>BTC/USDT</span>
      </div>
      <svg id="market-cross-chart" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT and BTC/USDT daily normalized comparison chart"></svg>
    </section>
    <section class="panel market-volume-panel">
      <div class="market-chart-head">
        <h2>KAS Exchange Volume 1D</h2>
        <div id="market-volume-status" class="market-status">Loading exchange volumes</div>
      </div>
      <div id="market-volume-legend" class="market-legend"></div>
      <svg id="market-volume-chart" class="market-chart" viewBox="0 0 720 244" role="img" aria-label="Daily KAS trading volume by exchange and total"></svg>
    </section>
    <section class="panel futures-panel">
      <div class="market-chart-head">
        <div class="market-title-row">
          <h2>KAS/USDT Futures Positioning</h2>
          <span class="market-source">Bybit linear perp</span>
        </div>
        <div id="futures-status" class="market-status">Loading futures positioning</div>
      </div>
      <div class="market-meta">
        <div class="context-item"><div class="context-label">Mark</div><div id="futures-mark" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Funding</div><div id="futures-funding" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Next funding</div><div id="futures-next-funding" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Open interest</div><div id="futures-open-interest" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">OI value</div><div id="futures-open-interest-value" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">24h futures volume</div><div id="futures-volume" class="context-value">unknown</div></div>
      </div>
    </section>
    <section class="panel futures-trend-panel">
      <div class="market-chart-head">
        <h2>KAS/USDT Futures Trend 7D</h2>
        <div id="futures-trend-status" class="market-status">Loading futures trend</div>
      </div>
      <div class="market-legend">
        <span style="color: #2563eb"><i></i>Open interest</span>
        <span style="color: #147a46"><i></i>Funding +</span>
        <span style="color: #b42318"><i></i>Funding -</span>
      </div>
      <svg id="futures-trend-chart" class="market-chart" viewBox="0 0 720 244" role="img" aria-label="KAS/USDT futures open interest and funding trend"></svg>
    </section>
    <section class="liquidation-grid">
      <section class="panel">
        <div class="market-chart-head">
          <h2>KAS/USDT Futures Liquidation Map 12H</h2>
          <div id="liquidation-status-12h" class="market-status">Loading liquidation map</div>
        </div>
        <div class="market-legend">
          <span style="color: #b42318"><i></i>Long liq</span>
          <span style="color: #2563eb"><i></i>Short liq</span>
          <span style="color: #111827"><i></i>Mark path</span>
        </div>
        <svg id="liquidation-chart-12h" class="market-chart" viewBox="0 0 720 244" role="img" aria-label="Estimated 12 hour KAS/USDT futures liquidation heatmap"></svg>
      </section>
      <section class="panel">
        <div class="market-chart-head">
          <h2>KAS/USDT Futures Liquidation Map 24H</h2>
          <div id="liquidation-status-24h" class="market-status">Loading liquidation map</div>
        </div>
        <div class="market-legend">
          <span style="color: #b42318"><i></i>Long liq</span>
          <span style="color: #2563eb"><i></i>Short liq</span>
          <span style="color: #111827"><i></i>Mark path</span>
        </div>
        <svg id="liquidation-chart-24h" class="market-chart" viewBox="0 0 720 244" role="img" aria-label="Estimated 24 hour KAS/USDT futures liquidation heatmap"></svg>
      </section>
      <section class="panel">
        <div class="market-chart-head">
          <h2>KAS/USDT Futures Liquidation Map 1W</h2>
          <div id="liquidation-status-1w" class="market-status">Loading liquidation map</div>
        </div>
        <div class="market-legend">
          <span style="color: #b42318"><i></i>Long liq</span>
          <span style="color: #2563eb"><i></i>Short liq</span>
          <span style="color: #111827"><i></i>Mark path</span>
        </div>
        <svg id="liquidation-chart-1w" class="market-chart" viewBox="0 0 720 244" role="img" aria-label="Estimated one week KAS/USDT futures liquidation heatmap"></svg>
      </section>
      <section class="panel">
        <div class="market-chart-head">
          <h2>KAS/USDT Futures Liquidation Map 1M</h2>
          <div id="liquidation-status-1m" class="market-status">Loading liquidation map</div>
        </div>
        <div class="market-legend">
          <span style="color: #b42318"><i></i>Long liq</span>
          <span style="color: #2563eb"><i></i>Short liq</span>
          <span style="color: #111827"><i></i>Mark path</span>
        </div>
        <svg id="liquidation-chart-1m" class="market-chart" viewBox="0 0 720 244" role="img" aria-label="Estimated one month KAS/USDT futures liquidation heatmap"></svg>
      </section>
    </section>
    <section class="chart-grid">
      <section class="panel">
        <div class="chart-head"><h2>Relay Activity</h2><div class="chart-value">{compact_number(progress.get('relay_blocks_in_window'))}</div></div>
        {relay_chart}
      </section>
      <section class="panel">
        <div class="chart-head"><h2>DAA Score</h2><div class="chart-value">{compact_number(grpc_metrics.get('virtual_daa_score'))}</div></div>
        {daa_chart}
      </section>
      <section class="panel">
        <div class="chart-head"><h2>Peer Floor</h2><div class="chart-value">{format_optional_number(benchmark_summary.get('min_peer_count'))}</div></div>
        {peer_chart}
      </section>
      <section class="panel">
        <div class="chart-head"><h2>Hashrate</h2><div class="chart-value">{format_hashrate(grpc_metrics.get('network_hashes_per_second'))}</div></div>
        {hashrate_chart}
      </section>
      <section class="panel">
        <div class="chart-head"><h2>Severity Timeline</h2><div class="chart-value">{html.escape(severity.upper())}</div></div>
        {severity_chart}
      </section>
    </section>
    <section class="panel">
      <div class="chart-head">
        <h2>Relay Intake</h2>
        <div class="chart-value">{compact_number(progress.get('relay_blocks_in_window'))}</div>
      </div>
      <div class="subtle">{html.escape(relay_detail)}</div>
      {relay_intake}
    </section>
    <section class="panel">
      <div class="chart-head">
        <h2>Block Processing</h2>
        <div class="chart-value">{html.escape(processed_rate_text)}</div>
      </div>
      <div class="subtle">{html.escape(processed_detail)}</div>
      {processed_chart}
    </section>
    <section class="panel">
      <div class="chart-head">
        <h2>Transaction Throughput</h2>
        <div class="chart-value">{html.escape(transaction_rate_text)}</div>
      </div>
      <div class="subtle">{html.escape(transaction_detail)}</div>
      {transaction_chart}
    </section>
    <section class="panel">
      <div class="chart-head">
        <h2>Mempool Activity</h2>
        <div class="chart-value">{compact_number(grpc_metrics.get('mempool_size'))}</div>
      </div>
      <div class="subtle">{html.escape(mempool_detail)}</div>
      {mempool_chart}
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Triage Queue</h2>
        {triage_items}
      </section>
      <section class="panel">
        <h2>Run Context</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Failed checks</div><div class="context-value">{html.escape(failure_text)}</div></div>
          <div class="context-item"><div class="context-label">Latest relay age</div><div class="context-value">{html.escape(latest_relay_text)}</div></div>
          <div class="context-item"><div class="context-label">Last alert</div><div class="context-value">{html.escape(str(last_alert_at))}</div></div>
          <div class="context-item"><div class="context-label">Recovery mode</div><div class="context-value">{html.escape(str(recovery.get('mode', 'unknown')))}</div></div>
        </div>
      </section>
    </section>
    <section class="panel">
      <h2>Command Center</h2>
      {commands}
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Checks</h2>
        <div class="check-grid">{check_pills}</div>
      </section>
      <section class="panel">
        <h2>Node Context</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Network</div><div class="context-value">{html.escape(str(network_text))}</div></div>
          <div class="context-item"><div class="context-label">Synced</div><div class="context-value">{html.escape(str(sync_text))}</div></div>
          <div class="context-item"><div class="context-label">Active peers</div><div class="context-value">{html.escape(str(grpc_metrics.get('active_peers', 'unknown')))}</div></div>
          <div class="context-item"><div class="context-label">Mempool</div><div class="context-value">{html.escape(str(grpc_metrics.get('mempool_size', 'unknown')))}</div></div>
        </div>
      </section>
    </section>
    <section class="panel">
      <h2>Check Details</h2>
      <table>
        <thead>{html_row(["State", "Check", "Detail"], "th")}</thead>
        <tbody>{checks}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Benchmark Trend</h2>
      <table>
        <tbody>{benchmark_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Recent Recovery</h2>
      <table>
        <thead>{html_row(["Started At", "Action", "Before", "After", "Failed Before", "Reason"], "th")}</thead>
        <tbody>{recovery_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Recent History</h2>
      <table>
        <thead>{html_row(["Checked At", "Severity", "Failed", "Peers", "Relay Blocks", "DAA"], "th")}</thead>
        <tbody>{history}</tbody>
      </table>
    </section>
  </main>
  <script>
    const marketConfig = {{
      tickerUrl: "https://api.bybit.com/v5/market/tickers?category=spot&symbol=KASUSDT",
      futuresTickerUrl: "https://api.bybit.com/v5/market/tickers?category=linear&symbol=KASUSDT",
      klines: [
        {{
          label: "15m",
          emaPeriod: 21,
          limit: 120,
          intervalMs: 15 * 60 * 1000,
          lookbackMs: 24 * 60 * 60 * 1000,
          chartId: "market-chart",
          statusId: "market-status",
          trendId: "market-trend-15m",
          rsiId: "market-rsi-15m",
          url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=15",
        }},
        {{
          label: "4h",
          emaPeriod: 12,
          limit: 48,
          intervalMs: 4 * 60 * 60 * 1000,
          lookbackMs: 7 * 24 * 60 * 60 * 1000,
          chartId: "market-chart-4h",
          statusId: "market-status-4h",
          trendId: "market-trend-4h",
          rsiId: "market-rsi-4h",
          url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=240",
        }},
        {{
          label: "1D",
          emaPeriod: 10,
          axisMode: "day",
          limit: 40,
          intervalMs: 24 * 60 * 60 * 1000,
          lookbackMonths: 1,
          chartId: "market-chart-1d",
          statusId: "market-status-1d",
          trendId: "market-trend-1d",
          rsiId: "market-rsi-1d",
          url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=D",
        }},
        {{
          label: "1W",
          emaPeriod: 13,
          axisMode: "month",
          limit: 60,
          intervalMs: 7 * 24 * 60 * 60 * 1000,
          lookbackMs: 365 * 24 * 60 * 60 * 1000,
          chartId: "market-chart-1w",
          statusId: "market-status-1w",
          trendId: "market-trend-1w",
          rsiId: "market-rsi-1w",
          url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=W",
        }},
        {{
          label: "1M",
          emaPeriod: 6,
          axisMode: "year",
          limit: 1000,
          chartId: "market-chart-1m",
          statusId: "market-status-1m",
          trendId: "market-trend-1m",
          rsiId: "market-rsi-1m",
          url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=M",
        }},
      ],
      cross: {{
        chartId: "market-cross-chart",
        statusId: "market-cross-status",
        axisMode: "day",
        series: [
          {{
            label: "KAS/USDT",
            color: "#b42318",
            url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=D&limit=32",
          }},
          {{
            label: "BTC/USDT",
            color: "#2563eb",
            url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=D&limit=32",
          }},
        ],
      }},
      volume: {{
        chartId: "market-volume-chart",
        statusId: "market-volume-status",
        legendId: "market-volume-legend",
        limit: 32,
        sources: [
          {{
            label: "Gate",
            color: "#2563eb",
            parser: "gate",
            url: "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=KAS_USDT&interval=1d&limit=32",
          }},
          {{
            label: "MEXC",
            color: "#16a34a",
            parser: "mexc",
            url: "https://api.mexc.com/api/v3/klines?symbol=KASUSDT&interval=1d&limit=32",
          }},
          {{
            label: "KuCoin",
            color: "#7c3aed",
            parser: "kucoin",
            url: "https://api.kucoin.com/api/v1/market/candles?type=1day&symbol=KAS-USDT",
          }},
          {{
            label: "Bybit",
            color: "#d97706",
            parser: "bybit",
            url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=D&limit=32",
          }},
          {{
            label: "Bitget",
            color: "#0891b2",
            parser: "bitget",
            url: "https://api.bitget.com/api/v2/spot/market/candles?symbol=KASUSDT&granularity=1day&limit=32",
          }},
          {{
            label: "Kraken",
            color: "#be123c",
            parser: "kraken",
            url: "https://api.kraken.com/0/public/OHLC?pair=KASUSD&interval=1440",
          }},
          {{
            label: "HTX",
            color: "#4b5563",
            parser: "htx",
            url: "https://api.huobi.pro/market/history/kline?symbol=kasusdt&period=1day&size=32",
          }},
        ],
      }},
      liquidations: [
        {{
          label: "12H",
          chartId: "liquidation-chart-12h",
          statusId: "liquidation-status-12h",
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=15&limit=48",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=15min&limit=48",
        }},
        {{
          label: "24H",
          chartId: "liquidation-chart-24h",
          statusId: "liquidation-status-24h",
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=30&limit=48",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=30min&limit=48",
        }},
        {{
          label: "1W",
          chartId: "liquidation-chart-1w",
          statusId: "liquidation-status-1w",
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=240&limit=42",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=4h&limit=42",
        }},
        {{
          label: "1M",
          chartId: "liquidation-chart-1m",
          statusId: "liquidation-status-1m",
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=D&limit=32",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=1d&limit=32",
        }},
      ],
      futuresTrend: {{
        chartId: "futures-trend-chart",
        statusId: "futures-trend-status",
        openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=4h&limit=42",
        fundingUrl: "https://api.bybit.com/v5/market/funding/history?category=linear&symbol=KASUSDT&limit=42",
      }},
    }};
    const marketSignals = new Map();

    function marketText(id, value) {{
      const element = document.getElementById(id);
      if (element) {{
        element.textContent = value;
      }}
    }}

    function marketNumber(value) {{
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }}

    function formatMarketPrice(value) {{
      const parsed = marketNumber(value);
      if (parsed === null) {{
        return "unknown";
      }}
      return "$" + parsed.toLocaleString(undefined, {{
        minimumFractionDigits: 5,
        maximumFractionDigits: 5,
      }});
    }}

    function formatMarketPercent(value) {{
      const parsed = marketNumber(value);
      if (parsed === null) {{
        return "unknown";
      }}
      return (parsed * 100).toLocaleString(undefined, {{
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }}) + "%";
    }}

    function formatFundingPercent(value) {{
      const parsed = marketNumber(value);
      if (parsed === null) {{
        return "unknown";
      }}
      return (parsed * 100).toLocaleString(undefined, {{
        minimumFractionDigits: 4,
        maximumFractionDigits: 4,
      }}) + "%";
    }}

    function formatMarketSignedPercent(value) {{
      const parsed = marketNumber(value);
      if (parsed === null) {{
        return "unknown";
      }}
      const prefix = parsed >= 0 ? "+" : "";
      return prefix + parsed.toLocaleString(undefined, {{
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }}) + "%";
    }}

    function formatMarketVolume(value) {{
      const parsed = marketNumber(value);
      if (parsed === null) {{
        return "unknown";
      }}
      if (parsed >= 1000000) {{
        return (parsed / 1000000).toLocaleString(undefined, {{ maximumFractionDigits: 2 }}) + "M KAS";
      }}
      if (parsed >= 1000) {{
        return (parsed / 1000).toLocaleString(undefined, {{ maximumFractionDigits: 1 }}) + "K KAS";
      }}
      return parsed.toLocaleString(undefined, {{ maximumFractionDigits: 2 }}) + " KAS";
    }}

    function formatMarketUsdt(value) {{
      const parsed = marketNumber(value);
      if (parsed === null) {{
        return "unknown";
      }}
      if (parsed >= 1000000) {{
        return "$" + (parsed / 1000000).toLocaleString(undefined, {{ maximumFractionDigits: 2 }}) + "M";
      }}
      if (parsed >= 1000) {{
        return "$" + (parsed / 1000).toLocaleString(undefined, {{ maximumFractionDigits: 1 }}) + "K";
      }}
      return "$" + parsed.toLocaleString(undefined, {{ maximumFractionDigits: 2 }});
    }}

    function formatMarketTime(value) {{
      const parsed = marketNumber(value);
      if (parsed === null) {{
        return "unknown";
      }}
      return new Date(parsed).toLocaleString([], {{
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }});
    }}

    function marketCacheKey(url) {{
      return "kaspa-watchtower-market:" + url;
    }}

    function marketRangeEndMs(config) {{
      const intervalMs = Number(config.intervalMs || 0);
      const now = Date.now();
      if (!Number.isFinite(intervalMs) || intervalMs <= 0) {{
        return now;
      }}
      return Math.floor(now / intervalMs) * intervalMs;
    }}

    function marketRangeStartMs(endMs, config) {{
      const lookbackMonths = Number(config.lookbackMonths || 0);
      if (Number.isFinite(lookbackMonths) && lookbackMonths > 0) {{
        const start = new Date(endMs);
        start.setMonth(start.getMonth() - lookbackMonths);
        return start.getTime();
      }}
      const lookbackMs = Number(config.lookbackMs || 0);
      if (Number.isFinite(lookbackMs) && lookbackMs > 0) {{
        return endMs - lookbackMs;
      }}
      return null;
    }}

    function marketKlineUrl(config) {{
      const url = new URL(config.url);
      const limit = Number(config.limit || 0);
      if (Number.isFinite(limit) && limit > 0) {{
        url.searchParams.set("limit", String(limit));
      }}
      const endMs = marketRangeEndMs(config);
      const startMs = marketRangeStartMs(endMs, config);
      if (startMs !== null) {{
        url.searchParams.set("start", String(startMs));
        url.searchParams.set("end", String(endMs));
      }}
      return url.toString();
    }}

    function readMarketCache(url) {{
      try {{
        const cached = window.localStorage.getItem(marketCacheKey(url));
        if (!cached) {{
          return null;
        }}
        const payload = JSON.parse(cached);
        return payload && payload.data ? payload : null;
      }} catch (error) {{
        return null;
      }}
    }}

    function writeMarketCache(url, payload) {{
      try {{
        window.localStorage.setItem(marketCacheKey(url), JSON.stringify({{
          cachedAt: Date.now(),
          data: payload,
        }}));
      }} catch (error) {{
        // Ignore cache quota and privacy-mode failures; live fetch remains the source of truth.
      }}
    }}

    async function fetchMarketJson(url) {{
      let lastError = null;
      for (let attempt = 0; attempt < 2; attempt += 1) {{
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 8000);
        try {{
          const response = await fetch(url, {{ cache: "no-store", signal: controller.signal }});
          if (!response.ok) {{
            throw new Error("HTTP " + response.status);
          }}
          const payload = await response.json();
          if (payload.retCode !== undefined && payload.retCode !== 0) {{
            throw new Error(payload.retMsg || "market API error");
          }}
          if (payload.code !== undefined && payload.code !== "00000" && payload.code !== "200000") {{
            throw new Error(payload.msg || "market API error");
          }}
          if (payload.status !== undefined && payload.status !== "ok") {{
            throw new Error(payload["err-msg"] || "market API error");
          }}
          if (Array.isArray(payload.error) && payload.error.length > 0) {{
            throw new Error(payload.error.join(", "));
          }}
          window.clearTimeout(timeout);
          writeMarketCache(url, payload);
          return payload;
        }} catch (error) {{
          lastError = error;
          window.clearTimeout(timeout);
        }}
      }}
      const cached = readMarketCache(url);
      if (cached) {{
        return {{
          ...cached.data,
          cachedAt: cached.cachedAt,
          fromCache: true,
        }};
      }}
      throw lastError || new Error("market API unavailable");
    }}

    function marketCandlesFromRows(rows) {{
      return rows
        .map((row) => ({{
          time: marketNumber(row[0]),
          open: marketNumber(row[1]),
          high: marketNumber(row[2]),
          low: marketNumber(row[3]),
          close: marketNumber(row[4]),
        }}))
        .filter((row) => row.open !== null && row.high !== null && row.low !== null && row.close !== null)
        .reverse();
    }}

    function marketEmaPoints(candles, period) {{
      const parsedPeriod = Number(period);
      if (!Number.isFinite(parsedPeriod) || parsedPeriod <= 0 || candles.length < 2) {{
        return [];
      }}
      const multiplier = 2 / (parsedPeriod + 1);
      let previous = candles[0].close;
      return candles.map((candle) => {{
        previous = candle.close * multiplier + previous * (1 - multiplier);
        return {{
          time: candle.time,
          value: previous,
        }};
      }});
    }}

    function marketTrendState(candles, emaPoints) {{
      if (candles.length < 2 || emaPoints.length < 2) {{
        return {{ tone: "", text: "Trend pending", detail: "Not enough EMA data" }};
      }}
      const latest = candles[candles.length - 1];
      const latestEma = emaPoints[emaPoints.length - 1].value;
      const slopeIndex = Math.max(0, emaPoints.length - 4);
      const previousEma = emaPoints[slopeIndex].value;
      const distancePct = latestEma ? ((latest.close - latestEma) / latestEma) * 100 : 0;
      const slopePct = previousEma ? ((latestEma - previousEma) / previousEma) * 100 : 0;
      const distanceText = formatMarketSignedPercent(distancePct);
      const slopeText = formatMarketSignedPercent(slopePct);
      if (latest.close > latestEma && slopePct >= 0) {{
        return {{
          tone: "up",
          text: "Uptrend",
          detail: "Close " + distanceText + " vs EMA; EMA slope " + slopeText,
        }};
      }}
      if (latest.close < latestEma && slopePct <= 0) {{
        return {{
          tone: "down",
          text: "Downtrend",
          detail: "Close " + distanceText + " vs EMA; EMA slope " + slopeText,
        }};
      }}
      return {{
        tone: "neutral",
        text: "Neutral",
        detail: "Close " + distanceText + " vs EMA; EMA slope " + slopeText,
      }};
    }}

    function marketTrendBadge(id, state) {{
      const element = document.getElementById(id);
      if (!element) {{
        return;
      }}
      element.textContent = state.text;
      element.title = state.detail;
      element.setAttribute("aria-label", state.text + ": " + state.detail);
      element.className = "market-trend-badge" + (state.tone ? " " + state.tone : "");
    }}

    function marketRsiValue(candles, period) {{
      const parsedPeriod = Number(period);
      if (!Number.isFinite(parsedPeriod) || parsedPeriod <= 0 || candles.length <= parsedPeriod) {{
        return null;
      }}
      let averageGain = 0;
      let averageLoss = 0;
      for (let index = 1; index <= parsedPeriod; index += 1) {{
        const change = candles[index].close - candles[index - 1].close;
        averageGain += Math.max(0, change);
        averageLoss += Math.max(0, -change);
      }}
      averageGain /= parsedPeriod;
      averageLoss /= parsedPeriod;
      for (let index = parsedPeriod + 1; index < candles.length; index += 1) {{
        const change = candles[index].close - candles[index - 1].close;
        averageGain = (averageGain * (parsedPeriod - 1) + Math.max(0, change)) / parsedPeriod;
        averageLoss = (averageLoss * (parsedPeriod - 1) + Math.max(0, -change)) / parsedPeriod;
      }}
      if (averageLoss === 0) {{
        return 100;
      }}
      if (averageGain === 0) {{
        return 0;
      }}
      const relativeStrength = averageGain / averageLoss;
      return 100 - 100 / (1 + relativeStrength);
    }}

    function marketRsiState(candles, period) {{
      const value = marketRsiValue(candles, period);
      if (value === null) {{
        return {{ tone: "", text: "RSI pending", detail: "Not enough RSI data" }};
      }}
      const rounded = value.toFixed(0);
      const detail = "RSI " + period + " = " + value.toFixed(1);
      if (value >= 70) {{
        return {{ tone: "hot", text: "RSI " + rounded + " Overbought", detail }};
      }}
      if (value <= 30) {{
        return {{ tone: "cool", text: "RSI " + rounded + " Oversold", detail }};
      }}
      return {{ tone: "neutral", text: "RSI " + rounded + " Neutral", detail }};
    }}

    function marketRsiBadge(id, state) {{
      const element = document.getElementById(id);
      if (!element) {{
        return;
      }}
      element.textContent = state.text;
      element.title = state.detail;
      element.setAttribute("aria-label", state.text + ": " + state.detail);
      element.className = "market-rsi-badge" + (state.tone ? " " + state.tone : "");
    }}

    function marketSignalState(candles, emaPoints, rsiValue) {{
      if (candles.length < 2 || emaPoints.length < 2) {{
        return {{ tone: "", text: "Waiting for candles" }};
      }}
      const latest = candles[candles.length - 1];
      const previous = candles[candles.length - 2];
      const latestEma = emaPoints[emaPoints.length - 1].value;
      const previousEma = emaPoints[emaPoints.length - 2].value;
      if (previous.close <= previousEma && latest.close > latestEma) {{
        return {{ tone: "up", text: "EMA cross up" }};
      }}
      if (previous.close >= previousEma && latest.close < latestEma) {{
        return {{ tone: "down", text: "EMA cross down" }};
      }}
      if (rsiValue !== null && rsiValue >= 70) {{
        return {{ tone: "warn", text: "RSI overbought" }};
      }}
      if (rsiValue !== null && rsiValue <= 30) {{
        return {{ tone: "cool", text: "RSI oversold" }};
      }}
      if (latest.close >= latestEma) {{
        return {{ tone: "", text: "Above EMA" }};
      }}
      return {{ tone: "", text: "Below EMA" }};
    }}

    function marketSignalWatch(label, state) {{
      marketSignals.set(label, state);
      const list = document.getElementById("market-signal-list");
      if (!list) {{
        return;
      }}
      list.replaceChildren();
      marketConfig.klines.forEach((config) => {{
        const signal = marketSignals.get(config.label) || {{ tone: "", text: "Waiting for candles" }};
        const row = document.createElement("div");
        row.className = "market-signal-row" + (signal.tone ? " " + signal.tone : "");

        const labelElement = document.createElement("span");
        labelElement.textContent = config.label;
        row.appendChild(labelElement);

        const textElement = document.createElement("strong");
        textElement.textContent = signal.text;
        row.appendChild(textElement);

        list.appendChild(row);
      }});
    }}

    function marketPad2(value) {{
      return String(value).padStart(2, "0");
    }}

    function marketAxisTimeLabel(time, mode) {{
      const date = new Date(time);
      const year = String(date.getFullYear());
      const month = marketPad2(date.getMonth() + 1);
      const day = marketPad2(date.getDate());
      if (mode === "year") {{
        return year;
      }}
      if (mode === "month") {{
        return year + "-" + month;
      }}
      if (mode === "day") {{
        return month + "/" + day;
      }}
      return date.toLocaleString([], {{
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }});
    }}

    function drawMarketCandles(rows, chartId, statusId, trendId, rsiId, labelText, emaPeriod, axisMode) {{
      const svg = document.getElementById(chartId);
      if (!svg) {{
        return;
      }}
      svg.replaceChildren();
      const candles = marketCandlesFromRows(rows);
      if (candles.length < 2) {{
        marketText(statusId, "Not enough candle data");
        marketTrendBadge(trendId, {{ tone: "", text: "Trend pending", detail: "Not enough candle data" }});
        marketRsiBadge(rsiId, {{ tone: "", text: "RSI pending", detail: "Not enough candle data" }});
        marketSignalWatch(labelText, {{ tone: "", text: "Not enough data" }});
        return;
      }}
      const width = 720;
      const height = 230;
      const leftPad = 24;
      const rightPad = 70;
      const topPad = 18;
      const bottomPad = 34;
      const highs = candles.map((row) => row.high);
      const lows = candles.map((row) => row.low);
      const high = Math.max(...highs);
      const low = Math.min(...lows);
      const span = high - low || 1;
      const chartWidth = width - leftPad - rightPad;
      const chartHeight = height - topPad - bottomPad;
      const step = chartWidth / candles.length;
      const bodyWidth = Math.max(4, Math.min(12, step * 0.56));
      const y = (price) => topPad + chartHeight - ((price - low) / span) * chartHeight;
      const ns = "http://www.w3.org/2000/svg";

      [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {{
        const line = document.createElementNS(ns, "line");
        const lineY = topPad + chartHeight * ratio;
        line.setAttribute("x1", String(leftPad));
        line.setAttribute("x2", String(width - rightPad));
        line.setAttribute("y1", String(lineY));
        line.setAttribute("y2", String(lineY));
        line.setAttribute("stroke", "#d9e1e8");
        line.setAttribute("stroke-width", "1");
        svg.appendChild(line);

        const price = high - span * ratio;
        const label = document.createElementNS(ns, "text");
        label.textContent = "$" + price.toFixed(5);
        label.setAttribute("x", String(width - rightPad + 10));
        label.setAttribute("y", String(lineY + 4));
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "11");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      [0, Math.floor((candles.length - 1) / 2), candles.length - 1].forEach((index) => {{
        const candle = candles[index];
        const x = leftPad + step * index + step / 2;
        const label = document.createElementNS(ns, "text");
        label.textContent = marketAxisTimeLabel(candle.time, axisMode);
        label.setAttribute("x", String(x));
        label.setAttribute("y", String(height - 9));
        label.setAttribute("text-anchor", index === 0 ? "start" : index === candles.length - 1 ? "end" : "middle");
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "10");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      candles.forEach((candle, index) => {{
        const x = leftPad + step * index + step / 2;
        const up = candle.close >= candle.open;
        const color = up ? "#147a46" : "#b42318";
        const wick = document.createElementNS(ns, "line");
        wick.setAttribute("x1", String(x));
        wick.setAttribute("x2", String(x));
        wick.setAttribute("y1", String(y(candle.high)));
        wick.setAttribute("y2", String(y(candle.low)));
        wick.setAttribute("stroke", color);
        wick.setAttribute("stroke-width", "2");
        wick.setAttribute("stroke-linecap", "round");
        svg.appendChild(wick);

        const body = document.createElementNS(ns, "rect");
        const openY = y(candle.open);
        const closeY = y(candle.close);
        body.setAttribute("x", String(x - bodyWidth / 2));
        body.setAttribute("y", String(Math.min(openY, closeY)));
        body.setAttribute("width", String(bodyWidth));
        body.setAttribute("height", String(Math.max(2, Math.abs(openY - closeY))));
        body.setAttribute("rx", "2");
        body.setAttribute("fill", color);
        body.setAttribute("opacity", up ? "0.92" : "0.82");
        svg.appendChild(body);
      }});

      const emaPoints = marketEmaPoints(candles, emaPeriod);
      if (emaPoints.length > 1) {{
        const emaPath = document.createElementNS(ns, "path");
        const d = emaPoints
          .map((point, index) => {{
            const lineX = leftPad + step * index + step / 2;
            return (index === 0 ? "M" : "L") + lineX.toFixed(1) + " " + y(point.value).toFixed(1);
          }})
          .join(" ");
        emaPath.setAttribute("d", d);
        emaPath.setAttribute("fill", "none");
        emaPath.setAttribute("stroke", "#d97706");
        emaPath.setAttribute("stroke-width", "3");
        emaPath.setAttribute("stroke-linecap", "round");
        emaPath.setAttribute("stroke-linejoin", "round");
        emaPath.setAttribute("class", "market-ema-line");
        svg.appendChild(emaPath);

        const emaLabel = document.createElementNS(ns, "text");
        emaLabel.textContent = String(emaPeriod) + "EMA";
        emaLabel.setAttribute("x", String(leftPad));
        emaLabel.setAttribute("y", String(topPad + 4));
        emaLabel.setAttribute("fill", "#d97706");
        emaLabel.setAttribute("font-size", "11");
        emaLabel.setAttribute("font-weight", "800");
        emaLabel.setAttribute("class", "market-ema-label");
        svg.appendChild(emaLabel);
      }}
      marketTrendBadge(trendId, marketTrendState(candles, emaPoints));
      marketRsiBadge(rsiId, marketRsiState(candles, 14));
      marketSignalWatch(labelText, marketSignalState(candles, emaPoints, marketRsiValue(candles, 14)));

      const latest = candles[candles.length - 1];
      marketText(statusId, labelText + " candles updated at " + new Date(latest.time).toLocaleTimeString());
    }}

    function drawMarketCrossChart(seriesRows, chartId, statusId, axisMode) {{
      const svg = document.getElementById(chartId);
      if (!svg) {{
        return;
      }}
      svg.replaceChildren();
      const series = seriesRows
        .map((item) => ({{
          label: item.label,
          color: item.color,
          candles: marketCandlesFromRows(item.rows),
        }}))
        .filter((item) => item.candles.length >= 2);
      if (series.length < 2) {{
        marketText(statusId, "Not enough cross data");
        return;
      }}
      const minLength = Math.min(...series.map((item) => item.candles.length));
      const normalized = series.map((item) => {{
        const candles = item.candles.slice(-minLength);
        const base = candles[0].close || 1;
        return {{
          label: item.label,
          color: item.color,
          points: candles.map((candle) => ({{
            time: candle.time,
            value: (candle.close / base) * 100,
          }})),
        }};
      }});
      const values = normalized.flatMap((item) => item.points.map((point) => point.value));
      const rawHigh = Math.max(...values);
      const rawLow = Math.min(...values);
      const rawSpan = rawHigh - rawLow || 1;
      const high = rawHigh + rawSpan * 0.08;
      const low = rawLow - rawSpan * 0.08;
      const span = high - low || 1;
      const width = 720;
      const height = 230;
      const leftPad = 34;
      const rightPad = 74;
      const topPad = 18;
      const bottomPad = 34;
      const chartWidth = width - leftPad - rightPad;
      const chartHeight = height - topPad - bottomPad;
      const step = chartWidth / Math.max(1, minLength - 1);
      const x = (index) => leftPad + step * index;
      const y = (value) => topPad + chartHeight - ((value - low) / span) * chartHeight;
      const ns = "http://www.w3.org/2000/svg";

      [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {{
        const lineY = topPad + chartHeight * ratio;
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(leftPad));
        line.setAttribute("x2", String(width - rightPad));
        line.setAttribute("y1", String(lineY));
        line.setAttribute("y2", String(lineY));
        line.setAttribute("stroke", "#d9e1e8");
        line.setAttribute("stroke-width", "1");
        svg.appendChild(line);

        const axisValue = high - span * ratio;
        const label = document.createElementNS(ns, "text");
        const change = axisValue - 100;
        label.textContent = (change >= 0 ? "+" : "") + change.toFixed(1) + "%";
        label.setAttribute("x", String(width - rightPad + 10));
        label.setAttribute("y", String(lineY + 4));
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "11");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      [0, Math.floor((minLength - 1) / 2), minLength - 1].forEach((index) => {{
        const point = normalized[0].points[index];
        const label = document.createElementNS(ns, "text");
        label.textContent = marketAxisTimeLabel(point.time, axisMode);
        label.setAttribute("x", String(x(index)));
        label.setAttribute("y", String(height - 9));
        label.setAttribute("text-anchor", index === 0 ? "start" : index === minLength - 1 ? "end" : "middle");
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "10");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      const zeroY = y(100);
      const zeroLine = document.createElementNS(ns, "line");
      zeroLine.setAttribute("x1", String(leftPad));
      zeroLine.setAttribute("x2", String(width - rightPad));
      zeroLine.setAttribute("y1", String(zeroY));
      zeroLine.setAttribute("y2", String(zeroY));
      zeroLine.setAttribute("stroke", "#8aa0ad");
      zeroLine.setAttribute("stroke-dasharray", "5 5");
      zeroLine.setAttribute("stroke-width", "1");
      svg.appendChild(zeroLine);

      normalized.forEach((item) => {{
        const path = document.createElementNS(ns, "path");
        const d = item.points
          .map((point, index) => (index === 0 ? "M" : "L") + x(index).toFixed(1) + " " + y(point.value).toFixed(1))
          .join(" ");
        path.setAttribute("d", d);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", item.color);
        path.setAttribute("stroke-width", "3");
        path.setAttribute("stroke-linecap", "round");
        path.setAttribute("stroke-linejoin", "round");
        svg.appendChild(path);

        const latest = item.points[item.points.length - 1];
        const marker = document.createElementNS(ns, "circle");
        marker.setAttribute("cx", String(x(item.points.length - 1)));
        marker.setAttribute("cy", String(y(latest.value)));
        marker.setAttribute("r", "4");
        marker.setAttribute("fill", item.color);
        svg.appendChild(marker);
      }});

      const latest = normalized[0].points[normalized[0].points.length - 1];
      const summary = normalized
        .map((item) => item.label.replace("/USDT", "") + " " + formatMarketSignedPercent(item.points[item.points.length - 1].value - 100))
        .join(" / ");
      marketText(statusId, summary + " at " + new Date(latest.time).toLocaleTimeString());
    }}

    function marketDayKey(time) {{
      const date = new Date(time);
      return date.getUTCFullYear() + "-" + marketPad2(date.getUTCMonth() + 1) + "-" + marketPad2(date.getUTCDate());
    }}

    function marketVolumePoint(time, volume) {{
      const parsedTime = marketNumber(time);
      const parsedVolume = marketNumber(volume);
      if (parsedTime === null || parsedVolume === null || parsedVolume < 0) {{
        return null;
      }}
      return {{
        time: parsedTime < 1000000000000 ? parsedTime * 1000 : parsedTime,
        volume: parsedVolume,
      }};
    }}

    function marketVolumeRows(payload, parser) {{
      let rows = [];
      if (parser === "gate") {{
        rows = Array.isArray(payload) ? payload.map((row) => marketVolumePoint(row[0], row[6])) : [];
      }} else if (parser === "mexc") {{
        rows = Array.isArray(payload) ? payload.map((row) => marketVolumePoint(row[0], row[5])) : [];
      }} else if (parser === "kucoin") {{
        rows = (((payload || {{}}).data || [])).map((row) => marketVolumePoint(row[0], row[5]));
      }} else if (parser === "bybit") {{
        rows = ((((payload || {{}}).result || {{}}).list || [])).map((row) => marketVolumePoint(row[0], row[5]));
      }} else if (parser === "bitget") {{
        rows = (((payload || {{}}).data || [])).map((row) => marketVolumePoint(row[0], row[5]));
      }} else if (parser === "kraken") {{
        const result = (payload || {{}}).result || {{}};
        const pairKey = Object.keys(result).find((key) => key !== "last");
        rows = pairKey ? (result[pairKey] || []).map((row) => marketVolumePoint(row[0], row[6])) : [];
      }} else if (parser === "htx") {{
        rows = (((payload || {{}}).data || [])).map((row) => marketVolumePoint(row.id, row.amount));
      }}
      return rows
        .filter((row) => row !== null)
        .sort((left, right) => left.time - right.time);
    }}

    function marketVolumeDataset(sourceRows, limit) {{
      const sourceLabels = sourceRows.map((item) => item.label);
      const sourceColors = new Map(sourceRows.map((item) => [item.label, item.color]));
      const byDay = new Map();
      sourceRows.forEach((source) => {{
        source.rows.forEach((row) => {{
          const key = marketDayKey(row.time);
          if (!byDay.has(key)) {{
            byDay.set(key, {{
              key,
              time: row.time,
              volumes: new Map(),
            }});
          }}
          const day = byDay.get(key);
          day.time = Math.min(day.time, row.time);
          day.volumes.set(source.label, (day.volumes.get(source.label) || 0) + row.volume);
        }});
      }});
      const days = Array.from(byDay.values())
        .sort((left, right) => left.time - right.time)
        .slice(-limit)
        .map((day) => {{
          const total = sourceLabels.reduce((sum, label) => sum + (day.volumes.get(label) || 0), 0);
          return {{
            ...day,
            total,
          }};
        }})
        .filter((day) => day.total > 0);
      return {{ days, sourceLabels, sourceColors }};
    }}

    function drawMarketVolumeChart(sourceRows, config) {{
      const svg = document.getElementById(config.chartId);
      if (!svg) {{
        return;
      }}
      svg.replaceChildren();
      const legend = document.getElementById(config.legendId);
      if (legend) {{
        legend.replaceChildren();
        config.sources.forEach((source) => {{
          const item = document.createElement("span");
          item.style.color = source.color;
          const swatch = document.createElement("i");
          item.appendChild(swatch);
          item.appendChild(document.createTextNode(source.label));
          legend.appendChild(item);
        }});
        const totalItem = document.createElement("span");
        totalItem.className = "total";
        totalItem.style.color = "#111827";
        const totalSwatch = document.createElement("i");
        totalItem.appendChild(totalSwatch);
        totalItem.appendChild(document.createTextNode("Total"));
        legend.appendChild(totalItem);
      }}

      const dataset = marketVolumeDataset(sourceRows, Number(config.limit || 32));
      if (dataset.days.length < 2) {{
        marketText(config.statusId, "Not enough exchange volume data");
        return;
      }}
      const width = 720;
      const height = 244;
      const leftPad = 42;
      const rightPad = 82;
      const topPad = 18;
      const bottomPad = 34;
      const chartWidth = width - leftPad - rightPad;
      const chartHeight = height - topPad - bottomPad;
      const high = Math.max(...dataset.days.map((day) => day.total)) || 1;
      const y = (value) => topPad + chartHeight - (value / high) * chartHeight;
      const step = chartWidth / dataset.days.length;
      const barWidth = Math.max(5, Math.min(18, step * 0.58));
      const ns = "http://www.w3.org/2000/svg";

      [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {{
        const lineY = topPad + chartHeight * ratio;
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(leftPad));
        line.setAttribute("x2", String(width - rightPad));
        line.setAttribute("y1", String(lineY));
        line.setAttribute("y2", String(lineY));
        line.setAttribute("stroke", "#d9e1e8");
        line.setAttribute("stroke-width", "1");
        svg.appendChild(line);

        const label = document.createElementNS(ns, "text");
        label.textContent = formatMarketVolume(high * (1 - ratio)).replace(" KAS", "");
        label.setAttribute("x", String(width - rightPad + 10));
        label.setAttribute("y", String(lineY + 4));
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "11");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      [0, Math.floor((dataset.days.length - 1) / 2), dataset.days.length - 1].forEach((index) => {{
        const day = dataset.days[index];
        const label = document.createElementNS(ns, "text");
        label.textContent = marketAxisTimeLabel(day.time, "day");
        label.setAttribute("x", String(leftPad + step * index + step / 2));
        label.setAttribute("y", String(height - 9));
        label.setAttribute("text-anchor", index === 0 ? "start" : index === dataset.days.length - 1 ? "end" : "middle");
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "10");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      dataset.days.forEach((day, index) => {{
        const x = leftPad + step * index + step / 2;
        let stacked = 0;
        dataset.sourceLabels.forEach((label) => {{
          const value = day.volumes.get(label) || 0;
          if (value <= 0) {{
            return;
          }}
          const next = stacked + value;
          const rect = document.createElementNS(ns, "rect");
          rect.setAttribute("x", String(x - barWidth / 2));
          rect.setAttribute("y", String(y(next)));
          rect.setAttribute("width", String(barWidth));
          rect.setAttribute("height", String(Math.max(1, y(stacked) - y(next))));
          rect.setAttribute("rx", "2");
          rect.setAttribute("fill", dataset.sourceColors.get(label) || "#66727f");
          rect.setAttribute("opacity", "0.88");
          const title = document.createElementNS(ns, "title");
          title.textContent = day.key + " " + label + " " + formatMarketVolume(value);
          rect.appendChild(title);
          svg.appendChild(rect);
          stacked = next;
        }});
      }});

      const totalPath = document.createElementNS(ns, "path");
      totalPath.setAttribute(
        "d",
        dataset.days
          .map((day, index) => {{
            const x = leftPad + step * index + step / 2;
            return (index === 0 ? "M" : "L") + x.toFixed(1) + " " + y(day.total).toFixed(1);
          }})
          .join(" ")
      );
      totalPath.setAttribute("fill", "none");
      totalPath.setAttribute("stroke", "#111827");
      totalPath.setAttribute("stroke-width", "3");
      totalPath.setAttribute("stroke-linecap", "round");
      totalPath.setAttribute("stroke-linejoin", "round");
      svg.appendChild(totalPath);

      const latest = dataset.days[dataset.days.length - 1];
      const available = sourceRows.filter((source) => source.rows.length > 0).length;
      marketText(
        config.statusId,
        "Total " + formatMarketVolume(latest.total) + " across " + available + "/" + config.sources.length + " venues at " + marketAxisTimeLabel(latest.time, "day")
      );
    }}

    function marketLinearCandlesFromRows(rows) {{
      return rows
        .map((row) => ({{
          time: marketNumber(row[0]),
          open: marketNumber(row[1]),
          high: marketNumber(row[2]),
          low: marketNumber(row[3]),
          close: marketNumber(row[4]),
          volume: marketNumber(row[5]) || 0,
          turnover: marketNumber(row[6]) || 0,
        }}))
        .filter((row) => row.time !== null && row.open !== null && row.high !== null && row.low !== null && row.close !== null)
        .reverse();
    }}

    function marketOpenInterestRows(payload) {{
      return ((((payload || {{}}).result || {{}}).list || []))
        .map((row) => ({{
          time: marketNumber(row.timestamp),
          openInterest: marketNumber(row.openInterest),
        }}))
        .filter((row) => row.time !== null && row.openInterest !== null)
        .sort((left, right) => left.time - right.time);
    }}

    function marketFundingRows(payload) {{
      return ((((payload || {{}}).result || {{}}).list || []))
        .map((row) => ({{
          time: marketNumber(row.fundingRateTimestamp),
          fundingRate: marketNumber(row.fundingRate),
        }}))
        .filter((row) => row.time !== null && row.fundingRate !== null)
        .sort((left, right) => left.time - right.time);
    }}

    function drawFuturesTrend(openInterestPayload, fundingPayload, config) {{
      const svg = document.getElementById(config.chartId);
      if (!svg) {{
        return;
      }}
      svg.replaceChildren();
      const oiRows = marketOpenInterestRows(openInterestPayload);
      const fundingRows = marketFundingRows(fundingPayload);
      if (oiRows.length < 2 || fundingRows.length < 2) {{
        marketText(config.statusId, "Not enough futures trend data");
        return;
      }}
      const width = 720;
      const height = 244;
      const leftPad = 42;
      const rightPad = 86;
      const topPad = 18;
      const bottomPad = 34;
      const fundingHeight = 52;
      const chartWidth = width - leftPad - rightPad;
      const oiHeight = height - topPad - bottomPad - fundingHeight;
      const fundingTop = topPad + oiHeight + 14;
      const fundingBase = fundingTop + fundingHeight / 2;
      const oiValues = oiRows.map((row) => row.openInterest);
      const rawHigh = Math.max(...oiValues);
      const rawLow = Math.min(...oiValues);
      const rawSpan = rawHigh - rawLow || 1;
      const oiHigh = rawHigh + rawSpan * 0.08;
      const oiLow = Math.max(0, rawLow - rawSpan * 0.08);
      const oiSpan = oiHigh - oiLow || 1;
      const maxFunding = Math.max(...fundingRows.map((row) => Math.abs(row.fundingRate))) || 0.0001;
      const ns = "http://www.w3.org/2000/svg";
      const xOi = (index) => leftPad + (chartWidth / Math.max(1, oiRows.length - 1)) * index;
      const yOi = (value) => topPad + oiHeight - ((value - oiLow) / oiSpan) * oiHeight;
      const xFunding = (index) => leftPad + (chartWidth / fundingRows.length) * index + chartWidth / fundingRows.length / 2;

      [0, 0.5, 1].forEach((ratio) => {{
        const lineY = topPad + oiHeight * ratio;
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(leftPad));
        line.setAttribute("x2", String(width - rightPad));
        line.setAttribute("y1", String(lineY));
        line.setAttribute("y2", String(lineY));
        line.setAttribute("stroke", "#d9e1e8");
        line.setAttribute("stroke-width", "1");
        svg.appendChild(line);

        const label = document.createElementNS(ns, "text");
        label.textContent = formatMarketVolume(oiHigh - oiSpan * ratio).replace(" KAS", "");
        label.setAttribute("x", String(width - rightPad + 10));
        label.setAttribute("y", String(lineY + 4));
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "11");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      const zeroLine = document.createElementNS(ns, "line");
      zeroLine.setAttribute("x1", String(leftPad));
      zeroLine.setAttribute("x2", String(width - rightPad));
      zeroLine.setAttribute("y1", String(fundingBase));
      zeroLine.setAttribute("y2", String(fundingBase));
      zeroLine.setAttribute("stroke", "#8aa0ad");
      zeroLine.setAttribute("stroke-dasharray", "4 4");
      zeroLine.setAttribute("stroke-width", "1");
      svg.appendChild(zeroLine);

      fundingRows.forEach((row, index) => {{
        const step = chartWidth / fundingRows.length;
        const barHeight = Math.max(1, Math.abs(row.fundingRate) / maxFunding * (fundingHeight / 2 - 3));
        const positive = row.fundingRate >= 0;
        const rect = document.createElementNS(ns, "rect");
        rect.setAttribute("x", String(xFunding(index) - Math.max(3, step * 0.36) / 2));
        rect.setAttribute("y", String(positive ? fundingBase - barHeight : fundingBase));
        rect.setAttribute("width", String(Math.max(3, step * 0.36)));
        rect.setAttribute("height", String(barHeight));
        rect.setAttribute("rx", "2");
        rect.setAttribute("fill", positive ? "#147a46" : "#b42318");
        rect.setAttribute("opacity", "0.82");
        svg.appendChild(rect);
      }});

      const oiPath = document.createElementNS(ns, "path");
      oiPath.setAttribute(
        "d",
        oiRows
          .map((row, index) => (index === 0 ? "M" : "L") + xOi(index).toFixed(1) + " " + yOi(row.openInterest).toFixed(1))
          .join(" ")
      );
      oiPath.setAttribute("fill", "none");
      oiPath.setAttribute("stroke", "#2563eb");
      oiPath.setAttribute("stroke-width", "3");
      oiPath.setAttribute("stroke-linecap", "round");
      oiPath.setAttribute("stroke-linejoin", "round");
      svg.appendChild(oiPath);

      [0, Math.floor((oiRows.length - 1) / 2), oiRows.length - 1].forEach((index) => {{
        const label = document.createElementNS(ns, "text");
        label.textContent = marketAxisTimeLabel(oiRows[index].time, "day");
        label.setAttribute("x", String(xOi(index)));
        label.setAttribute("y", String(height - 9));
        label.setAttribute("text-anchor", index === 0 ? "start" : index === oiRows.length - 1 ? "end" : "middle");
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "10");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      const latestOi = oiRows[oiRows.length - 1];
      const latestFunding = fundingRows[fundingRows.length - 1];
      marketText(config.statusId, "OI " + formatMarketVolume(latestOi.openInterest) + " / funding " + formatFundingPercent(latestFunding.fundingRate));
    }}

    function nearestOpenInterest(rows, time) {{
      if (!rows.length) {{
        return null;
      }}
      let nearest = rows[0];
      let distance = Math.abs(rows[0].time - time);
      rows.forEach((row) => {{
        const rowDistance = Math.abs(row.time - time);
        if (rowDistance < distance) {{
          nearest = row;
          distance = rowDistance;
        }}
      }});
      return nearest.openInterest;
    }}

    function buildLiquidationCells(candles, openInterestRows, priceLow, priceHigh, bins) {{
      const leverages = [
        {{ value: 10, weight: 1.2 }},
        {{ value: 25, weight: 1.0 }},
        {{ value: 50, weight: 0.85 }},
      ];
      const span = priceHigh - priceLow || 1;
      const cells = candles.map(() => Array.from({{ length: bins }}, () => ({{ long: 0, short: 0 }})));
      let maxIntensity = 0;
      let previousOi = null;
      candles.forEach((candle, index) => {{
        const oi = nearestOpenInterest(openInterestRows, candle.time);
        const oiDelta = oi !== null && previousOi !== null ? Math.abs(oi - previousOi) * candle.close : 0;
        if (oi !== null) {{
          previousOi = oi;
        }}
        const base = Math.log10(Math.max(1, candle.turnover + oiDelta + candle.volume * candle.close));
        leverages.forEach((leverage) => {{
          const longPrice = candle.close * (1 - 1 / leverage.value);
          const shortPrice = candle.close * (1 + 1 / leverage.value);
          [
            {{ price: longPrice, side: "long" }},
            {{ price: shortPrice, side: "short" }},
          ].forEach((point) => {{
            const rawBin = Math.round(((point.price - priceLow) / span) * (bins - 1));
            if (rawBin < 0 || rawBin >= bins) {{
              return;
            }}
            [-1, 0, 1].forEach((offset) => {{
              const bin = rawBin + offset;
              if (bin < 0 || bin >= bins) {{
                return;
              }}
              const decay = offset === 0 ? 1 : 0.42;
              const value = base * leverage.weight * decay;
              cells[index][bin][point.side] += value;
              maxIntensity = Math.max(maxIntensity, cells[index][bin].long + cells[index][bin].short);
            }});
          }});
        }});
      }});
      return {{ cells, maxIntensity: maxIntensity || 1 }};
    }}

    function drawLiquidationMap(klinePayload, openInterestPayload, config) {{
      const svg = document.getElementById(config.chartId);
      if (!svg) {{
        return;
      }}
      svg.replaceChildren();
      const candles = marketLinearCandlesFromRows((((klinePayload || {{}}).result || {{}}).list || []));
      const oiRows = marketOpenInterestRows(openInterestPayload);
      if (candles.length < 2) {{
        marketText(config.statusId, "Not enough futures data");
        return;
      }}
      const liquidationPrices = candles.flatMap((candle) => [candle.close * 0.98, candle.close * 0.96, candle.close * 0.9, candle.close * 1.02, candle.close * 1.04, candle.close * 1.1]);
      const lows = candles.map((row) => row.low).concat(liquidationPrices);
      const highs = candles.map((row) => row.high).concat(liquidationPrices);
      const rawLow = Math.min(...lows);
      const rawHigh = Math.max(...highs);
      const rawSpan = rawHigh - rawLow || 1;
      const priceLow = Math.max(0, rawLow - rawSpan * 0.04);
      const priceHigh = rawHigh + rawSpan * 0.04;
      const bins = 30;
      const heatmap = buildLiquidationCells(candles, oiRows, priceLow, priceHigh, bins);
      const width = 720;
      const height = 244;
      const leftPad = 34;
      const rightPad = 74;
      const topPad = 18;
      const bottomPad = 34;
      const chartWidth = width - leftPad - rightPad;
      const chartHeight = height - topPad - bottomPad;
      const step = chartWidth / candles.length;
      const binHeight = chartHeight / bins;
      const ns = "http://www.w3.org/2000/svg";
      const y = (price) => topPad + chartHeight - ((price - priceLow) / (priceHigh - priceLow || 1)) * chartHeight;

      [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {{
        const lineY = topPad + chartHeight * ratio;
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(leftPad));
        line.setAttribute("x2", String(width - rightPad));
        line.setAttribute("y1", String(lineY));
        line.setAttribute("y2", String(lineY));
        line.setAttribute("stroke", "#d9e1e8");
        line.setAttribute("stroke-width", "1");
        svg.appendChild(line);

        const price = priceHigh - (priceHigh - priceLow) * ratio;
        const label = document.createElementNS(ns, "text");
        label.textContent = "$" + price.toFixed(5);
        label.setAttribute("x", String(width - rightPad + 10));
        label.setAttribute("y", String(lineY + 4));
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "11");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      [0, Math.floor((candles.length - 1) / 2), candles.length - 1].forEach((index) => {{
        const candle = candles[index];
        const label = document.createElementNS(ns, "text");
        label.textContent = marketAxisTimeLabel(candle.time, config.label === "1M" ? "day" : undefined);
        label.setAttribute("x", String(leftPad + step * index + step / 2));
        label.setAttribute("y", String(height - 9));
        label.setAttribute("text-anchor", index === 0 ? "start" : index === candles.length - 1 ? "end" : "middle");
        label.setAttribute("fill", "#66727f");
        label.setAttribute("font-size", "10");
        label.setAttribute("font-weight", "700");
        label.setAttribute("class", "market-axis-label");
        svg.appendChild(label);
      }});

      heatmap.cells.forEach((column, columnIndex) => {{
        column.forEach((cell, bin) => {{
          const intensity = cell.long + cell.short;
          if (intensity <= 0) {{
            return;
          }}
          const longDominant = cell.long >= cell.short;
          const opacity = Math.max(0.12, Math.min(0.88, intensity / heatmap.maxIntensity));
          const rect = document.createElementNS(ns, "rect");
          rect.setAttribute("x", String(leftPad + step * columnIndex));
          rect.setAttribute("y", String(topPad + chartHeight - (bin + 1) * binHeight));
          rect.setAttribute("width", String(Math.max(1, step + 0.5)));
          rect.setAttribute("height", String(Math.max(1, binHeight + 0.5)));
          rect.setAttribute("fill", longDominant ? "#b42318" : "#2563eb");
          rect.setAttribute("opacity", String(opacity));
          svg.appendChild(rect);
        }});
      }});

      const markPath = document.createElementNS(ns, "path");
      markPath.setAttribute(
        "d",
        candles
          .map((candle, index) => {{
            const x = leftPad + step * index + step / 2;
            return (index === 0 ? "M" : "L") + x.toFixed(1) + " " + y(candle.close).toFixed(1);
          }})
          .join(" ")
      );
      markPath.setAttribute("fill", "none");
      markPath.setAttribute("stroke", "#111827");
      markPath.setAttribute("stroke-width", "2.5");
      markPath.setAttribute("stroke-linecap", "round");
      markPath.setAttribute("stroke-linejoin", "round");
      svg.appendChild(markPath);

      const latest = candles[candles.length - 1];
      marketText(config.statusId, "Estimated from Bybit linear OI/candles; latest " + formatMarketPrice(latest.close));
    }}

    async function refreshMarketChart(config) {{
      try {{
        const payload = await fetchMarketJson(marketKlineUrl(config));
        drawMarketCandles(((payload.result || {{}}).list || []), config.chartId, config.statusId, config.trendId, config.rsiId, config.label, config.emaPeriod, config.axisMode);
      }} catch (error) {{
        marketText(config.statusId, "KAS/USDT " + config.label + " candles unavailable");
        marketTrendBadge(config.trendId, {{ tone: "", text: "Trend pending", detail: "Market candles unavailable" }});
        marketRsiBadge(config.rsiId, {{ tone: "", text: "RSI pending", detail: "Market candles unavailable" }});
        marketSignalWatch(config.label, {{ tone: "", text: "Unavailable" }});
      }}
    }}

    async function refreshMarketCrossChart() {{
      try {{
        const payloads = await Promise.all(marketConfig.cross.series.map((item) => fetchMarketJson(item.url)));
        const seriesRows = payloads.map((payload, index) => ({{
          label: marketConfig.cross.series[index].label,
          color: marketConfig.cross.series[index].color,
          rows: ((payload.result || {{}}).list || []),
        }}));
        drawMarketCrossChart(seriesRows, marketConfig.cross.chartId, marketConfig.cross.statusId, marketConfig.cross.axisMode);
      }} catch (error) {{
        marketText(marketConfig.cross.statusId, "KAS/BTC daily cross unavailable");
      }}
    }}

    async function refreshMarketVolumeChart() {{
      const sourceRows = await Promise.all(marketConfig.volume.sources.map(async (source) => {{
        try {{
          const payload = await fetchMarketJson(source.url);
          return {{
            label: source.label,
            color: source.color,
            rows: marketVolumeRows(payload, source.parser),
          }};
        }} catch (error) {{
          return {{
            label: source.label,
            color: source.color,
            rows: [],
          }};
        }}
      }}));
      drawMarketVolumeChart(sourceRows, marketConfig.volume);
    }}

    async function refreshLiquidationMap(config) {{
      try {{
        const payloads = await Promise.all([
          fetchMarketJson(config.klineUrl),
          fetchMarketJson(config.openInterestUrl),
        ]);
        drawLiquidationMap(payloads[0], payloads[1], config);
      }} catch (error) {{
        marketText(config.statusId, "KAS/USDT futures liquidation map unavailable");
      }}
    }}

    async function refreshFuturesPositioning() {{
      try {{
        const payload = await fetchMarketJson(marketConfig.futuresTickerUrl);
        const ticker = ((payload.result || {{}}).list || [])[0] || {{}};
        marketText("futures-mark", formatMarketPrice(ticker.markPrice || ticker.lastPrice));
        marketText("futures-funding", formatFundingPercent(ticker.fundingRate));
        marketText("futures-next-funding", formatMarketTime(ticker.nextFundingTime));
        marketText("futures-open-interest", formatMarketVolume(ticker.openInterest));
        marketText("futures-open-interest-value", formatMarketUsdt(ticker.openInterestValue));
        marketText("futures-volume", formatMarketVolume(ticker.volume24h));
        const updatedPrefix = payload.fromCache ? "cached " : "";
        marketText("futures-status", updatedPrefix + "linear perp updated at " + new Date(Number(payload.cachedAt || payload.time || Date.now())).toLocaleTimeString());
      }} catch (error) {{
        marketText("futures-status", "KAS/USDT futures positioning unavailable");
      }}
    }}

    async function refreshFuturesTrend() {{
      try {{
        const payloads = await Promise.all([
          fetchMarketJson(marketConfig.futuresTrend.openInterestUrl),
          fetchMarketJson(marketConfig.futuresTrend.fundingUrl),
        ]);
        drawFuturesTrend(payloads[0], payloads[1], marketConfig.futuresTrend);
      }} catch (error) {{
        marketText(marketConfig.futuresTrend.statusId, "KAS/USDT futures trend unavailable");
      }}
    }}

    async function refreshMarketWatch() {{
      try {{
        const tickerPayload = await fetchMarketJson(marketConfig.tickerUrl);
        const ticker = ((tickerPayload.result || {{}}).list || [])[0] || {{}};
        marketText("market-last", formatMarketPrice(ticker.lastPrice));
        marketText("market-high", formatMarketPrice(ticker.highPrice24h));
        marketText("market-low", formatMarketPrice(ticker.lowPrice24h));
        marketText("market-volume", formatMarketVolume(ticker.volume24h));
        const updatedPrefix = tickerPayload.fromCache ? "cached " : "";
        marketText("market-updated", updatedPrefix + new Date(Number(tickerPayload.cachedAt || tickerPayload.time || Date.now())).toLocaleTimeString());
        const change = document.getElementById("market-change");
        if (change) {{
          const changeValue = marketNumber(ticker.price24hPcnt);
          change.textContent = "24h " + formatMarketPercent(ticker.price24hPcnt);
          change.classList.toggle("up", changeValue !== null && changeValue >= 0);
          change.classList.toggle("down", changeValue !== null && changeValue < 0);
        }}
      }} catch (error) {{
        marketText("market-last", "Unavailable");
        marketText("market-change", "Market API unavailable");
      }}
      await Promise.all([
        ...marketConfig.klines.map(refreshMarketChart),
        refreshMarketCrossChart(),
        refreshMarketVolumeChart(),
        refreshFuturesPositioning(),
        refreshFuturesTrend(),
        ...marketConfig.liquidations.map(refreshLiquidationMap),
      ]);
    }}

    refreshMarketWatch();
    window.setInterval(refreshMarketWatch, 30000);

    document.querySelectorAll(".command-copy").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const command = button.dataset.copy || "";
        try {{
          if (navigator.clipboard && window.isSecureContext) {{
            await navigator.clipboard.writeText(command);
          }} else {{
            const textArea = document.createElement("textarea");
            textArea.value = command;
            textArea.style.position = "fixed";
            textArea.style.left = "-9999px";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            document.execCommand("copy");
            document.body.removeChild(textArea);
          }}
          button.classList.add("copied");
          button.textContent = "Copied";
          window.setTimeout(() => {{
            button.classList.remove("copied");
            button.textContent = "Copy";
          }}, 1600);
        }} catch (error) {{
          button.textContent = "Copy failed";
          window.setTimeout(() => {{
            button.textContent = "Copy";
          }}, 1600);
        }}
      }});
    }});
  </script>
</body>
</html>
"""
    path.write_text(page, encoding="utf-8")


def alert(config: dict) -> int:
    state_path = Path(config.get("state_path") or DEFAULT_CONFIG["state_path"])
    status_page_path = Path(config.get("status_page_path") or DEFAULT_CONFIG["status_page_path"])
    canvas_status_page = config.get("canvas_status_page_path") or DEFAULT_CONFIG["canvas_status_page_path"]
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    metrics_path = Path(config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"])
    benchmark_summary = build_benchmark_summary(benchmark_path, limit=48)
    thresholds = config.get("thresholds", {})
    repeat_minutes = float(thresholds.get("alert_repeat_minutes", 60))
    state = load_state(state_path)
    report = build_report(config)
    apply_stateful_checks(report, state, config)
    previous_status = state.get("status")
    previous_severity = state.get("severity")
    previous_report = state.get("last_report") or {}
    previous_synced = (previous_report.get("grpc_metrics") or {}).get("is_synced")
    current_synced = (report.get("grpc_metrics") or {}).get("is_synced")
    sync_completed = previous_synced is False and current_synced is True
    event = "sync_completed" if sync_completed else None
    should_emit = should_emit_alert(state, report, repeat_minutes) or sync_completed
    history = state.get("history", [])
    history.append(history_item(report))
    history_limit = positive_int((config.get("retention") or {}).get("state_history_entries"), 100)
    history = history[-history_limit:]
    state.update(
        {
            "status": report["status"],
            "severity": report["severity"],
            "checked_at": report["checked_at"],
            "last_report": report,
            "history": history,
        }
    )
    if should_emit:
        state["last_alert_at"] = report["checked_at"]
    save_state(state_path, state)
    recovery_records = recent_recovery_records(config)
    write_status_page(status_page_path, report, state, benchmark_path, recovery_records)
    if canvas_status_page:
        write_status_page(Path(canvas_status_page), report, state, benchmark_path, recovery_records)
    recovery_summary = build_recovery_summary(recovery_history_path(config))
    write_prometheus_metrics(metrics_path, report, benchmark_summary, recovery_summary)

    if should_emit:
        print(format_alert(report, previous_status, previous_severity, event=event))
    return 0 if report["status"] == "ok" else 1


def format_alert(
    report: dict[str, Any],
    previous_status: str | None = None,
    previous_severity: str | None = None,
    event: str | None = None,
) -> str:
    failed_checks = [check for check in report["checks"] if not check["ok"]]
    if event == "sync_completed":
        title = f"Kaspa watchtower: {report['node_name']} sync completed"
    elif report["severity"] == "ok" and previous_status and previous_status != "ok":
        title = f"Kaspa watchtower: {report['node_name']} recovered"
    elif report["severity"] == "critical":
        title = f"Kaspa watchtower: {report['node_name']} critical"
    elif report["severity"] == "warn":
        title = f"Kaspa watchtower: {report['node_name']} warning"
    else:
        title = f"Kaspa watchtower: {report['node_name']} ok"

    lines = [
        title,
        f"checked_at={report['checked_at']}",
    ]
    if previous_status:
        lines.append(f"previous_status={previous_status}")
    if previous_severity:
        lines.append(f"previous_severity={previous_severity}")

    if failed_checks:
        lines.append("원인:")
        for check in failed_checks:
            lines.append(f"- {check['name']}: {check['detail']}")
    elif event == "sync_completed":
        lines.append("상태: mainnet sync completed")
        lines.append("Next: set thresholds.require_synced=true for strict production monitoring")
    elif report["severity"] == "ok":
        lines.append("상태: 모든 체크 정상")

    if report["latest_throughput"]:
        lines.append(f"Throughput: {report['latest_throughput']}")
    grpc_metrics = report.get("grpc_metrics") or {}
    if grpc_metrics.get("ok"):
        lines.append(
            "gRPC: "
            f"synced={grpc_metrics.get('is_synced')} "
            f"peers={grpc_metrics.get('peer_count')} "
            f"network={grpc_metrics.get('network_id')} "
            f"daa={grpc_metrics.get('virtual_daa_score')}"
        )
    progress = report["progress"]
    lines.append(
        "Progress: "
        f"{progress['relay_blocks_in_window']} blocks / "
        f"{progress['relay_events_in_window']} events in "
        f"{progress['window_minutes']:g}m"
    )
    sync_progress = report.get("sync_progress") or {}
    if sync_progress.get("active"):
        lines.append(
            "Sync progress: "
            f"{sync_progress.get('detail', 'unknown')} "
            f"(daa={format_optional_rate(sync_progress.get('daa_rate_per_hour'))}, "
            f"blocks={format_optional_rate(sync_progress.get('block_rate_per_hour'))}, "
            f"headers={format_optional_rate(sync_progress.get('header_rate_per_hour'))})"
        )
    recovery = report.get("recovery") or {}
    if recovery.get("action") != "none":
        lines.append(
            "Recovery: "
            f"{recovery.get('action')} "
            f"(mode={recovery.get('mode')}, restart_command_configured={recovery.get('restart_command_configured')})"
        )
    return "\n".join(lines)


def format_summary(report: dict[str, Any]) -> str:
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
    sync_progress = report.get("sync_progress") or {}
    disk = report.get("disk") or {}
    failed = failed_check_names(report)
    failed_text = ", ".join(failed) if failed else "none"
    latest_relay_age = progress.get("latest_relay_age_seconds")
    latest_relay_age_text = "unknown" if latest_relay_age is None else f"{latest_relay_age}s"
    disk_text = "unknown"
    if disk.get("exists"):
        disk_text = f"{disk.get('free_gb')} GiB ({disk.get('free_percent')}%)"

    lines = [
        f"Kaspa watchtower summary: {report['node_name']}",
        f"status={report['status']} severity={report['severity']} checked_at={report['checked_at']}",
        (
            "grpc="
            f"synced={grpc_metrics.get('is_synced', 'unknown')} "
            f"peers={grpc_metrics.get('peer_count', 'unknown')} "
            f"active={grpc_metrics.get('active_peers', 'unknown')} "
            f"network={grpc_metrics.get('network_id', 'unknown')} "
            f"daa={grpc_metrics.get('virtual_daa_score', 'unknown')} "
            f"mempool={grpc_metrics.get('mempool_size', 'unknown')} "
            f"tips={grpc_metrics.get('tip_count', 'unknown')} "
            f"hashrate={format_hashrate(grpc_metrics.get('network_hashes_per_second'))}"
        ),
        (
            "dag="
            f"headers={grpc_metrics.get('header_count', 'unknown')} "
            f"virtual_parents={grpc_metrics.get('virtual_parent_count', 'unknown')} "
            f"difficulty={grpc_metrics.get('difficulty', 'unknown')} "
            f"pruning={short_hash(grpc_metrics.get('pruning_point_hash'))}"
        ),
        (
            "progress="
            f"{progress.get('relay_blocks_in_window', 0)} relay blocks / "
            f"{progress.get('relay_events_in_window', 0)} events in "
            f"{progress.get('window_minutes', 'unknown')}m, "
            f"latest_relay_age={latest_relay_age_text}"
        ),
        f"processed={format_processed_progress(progress)}",
        f"disk_free={disk_text}",
        f"failed_checks={failed_text}",
    ]
    sync_progress = report.get("sync_progress") or {}
    if sync_progress.get("active"):
        lines.insert(
            -2,
            (
                "sync_progress="
                f"{sync_progress.get('detail', 'unknown')} "
                f"daa_rate={format_optional_rate(sync_progress.get('daa_rate_per_hour'))} "
                f"block_rate={format_optional_rate(sync_progress.get('block_rate_per_hour'))} "
                f"header_rate={format_optional_rate(sync_progress.get('header_rate_per_hour'))}"
            ),
        )
    recovery = report.get("recovery") or {}
    if recovery.get("action") != "none":
        lines.append(
            "recovery="
            f"{recovery.get('action')} "
            f"mode={recovery.get('mode')} "
            f"restart_configured={recovery.get('restart_command_configured')}"
        )
    return "\n".join(lines)


def format_sync_report(report: dict[str, Any], benchmark_summary: dict[str, Any]) -> str:
    grpc_metrics = report.get("grpc_metrics") or {}
    sync_progress = report.get("sync_progress") or {}
    failed = failed_check_names(report)
    failed_text = ", ".join(failed) if failed else "none"
    return "\n".join(
        [
            f"Kaspa sync report: {report['node_name']}",
            f"checked_at={report['checked_at']} status={report['status']} severity={report['severity']}",
            (
                "node="
                f"network={grpc_metrics.get('network_id', 'unknown')} "
                f"synced={grpc_metrics.get('is_synced', 'unknown')} "
                f"peers={grpc_metrics.get('peer_count', 'unknown')} "
                f"active={grpc_metrics.get('active_peers', 'unknown')}"
            ),
            (
                "dag="
                f"daa={grpc_metrics.get('virtual_daa_score', 'unknown')} "
                f"blocks={grpc_metrics.get('block_count', 'unknown')} "
                f"headers={grpc_metrics.get('header_count', 'unknown')} "
                f"tips={grpc_metrics.get('tip_count', 'unknown')}"
            ),
            f"sync_progress={sync_progress.get('detail', 'inactive')}",
            (
                "sync_rates="
                f"daa={format_optional_rate(sync_progress.get('daa_rate_per_hour'))} "
                f"blocks={format_optional_rate(sync_progress.get('block_rate_per_hour'))} "
                f"headers={format_optional_rate(sync_progress.get('header_rate_per_hour'))}"
            ),
            f"benchmark_window={benchmark_summary.get('window', 'unknown')}",
            (
                "benchmark_rates="
                f"daa={benchmark_summary.get('daa_rate', 'unknown')} "
                f"blocks={benchmark_summary.get('block_rate', 'unknown')} "
                f"relay={benchmark_summary.get('relay_rate', 'unknown')}"
            ),
            f"failed_checks={failed_text}",
        ]
    )


def format_diagnostics_summary(report: dict[str, Any]) -> str:
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
    disk = report.get("disk") or {}
    recovery = report.get("recovery") or {}
    failed = failed_check_names(report)
    failed_text = ",".join(failed) if failed else "none"
    severity = report.get("severity", "unknown")
    if severity == "ok":
        next_action = "no immediate action"
    elif severity == "critical":
        next_action = "review failed checks and run recovery dry-run before approved restart"
    else:
        next_action = "inspect warning checks and confirm trend before recovery"
    latest_relay_age = progress.get("latest_relay_age_seconds")
    latest_relay_age_text = "unknown" if latest_relay_age is None else f"{latest_relay_age}s"
    disk_text = "unknown"
    if disk.get("exists"):
        disk_text = f"{disk.get('free_gb')} GiB ({disk.get('free_percent')}%)"
    return "\n".join(
        [
            f"Kaspa diagnostics summary: {report.get('node_name', 'unknown')}",
            f"checked_at={report.get('checked_at', 'unknown')}",
            f"status={report.get('status', 'unknown')} severity={severity}",
            f"failed_checks={failed_text}",
            (
                "grpc="
                f"network={grpc_metrics.get('network_id', 'unknown')} "
                f"synced={grpc_metrics.get('is_synced', 'unknown')} "
                f"peers={grpc_metrics.get('peer_count', 'unknown')} "
                f"active={grpc_metrics.get('active_peers', 'unknown')} "
                f"daa={grpc_metrics.get('virtual_daa_score', 'unknown')} "
                f"hashrate={format_hashrate(grpc_metrics.get('network_hashes_per_second'))}"
            ),
            (
                "relay="
                f"{progress.get('relay_blocks_in_window', 0)} blocks / "
                f"{progress.get('relay_events_in_window', 0)} events in "
                f"{progress.get('window_minutes', 'unknown')}m "
                f"latest_age={latest_relay_age_text}"
            ),
            f"processed={format_processed_progress(progress)}",
            f"disk_free={disk_text}",
            (
                "recovery="
                f"action={recovery.get('action', 'unknown')} "
                f"mode={recovery.get('mode', 'unknown')} "
                f"restart_configured={recovery.get('restart_command_configured', 'unknown')}"
            ),
            f"next={next_action}",
            "sanitized=true",
        ]
    )


def diagnostics_summary(config: dict) -> int:
    report, _state = build_stateful_report(config)
    print(format_diagnostics_summary(report))
    return 0


def format_incident_report(report: dict[str, Any]) -> str:
    failed = failed_check_names(report)
    failed_text = ", ".join(failed) if failed else "none"
    recovery = report.get("recovery") or {}
    return "\n".join(
        [
            f"# Kaspa Watchtower Incident Report: {report.get('node_name', 'unknown')}",
            "",
            "## Verdict",
            "",
            f"- checked_at: `{report.get('checked_at', 'unknown')}`",
            f"- status: `{report.get('status', 'unknown')}`",
            f"- severity: `{report.get('severity', 'unknown')}`",
            f"- failed_checks: `{failed_text}`",
            f"- recovery_action: `{recovery.get('action', 'unknown')}`",
            f"- restart_configured: `{recovery.get('restart_command_configured', 'unknown')}`",
            "",
            "## Sanitized Summary",
            "",
            "```text",
            format_diagnostics_summary(report),
            "```",
            "",
            "## Operator Notes",
            "",
            "- Review failed checks against local logs and dashboard state.",
            "- Run recovery dry-run before any approved restart.",
            "- Attach diagnostics archives only after checking them for local paths or secrets.",
            "",
            "sanitized: true",
        ]
    )


def incident_report(config: dict) -> int:
    report, _state = build_stateful_report(config)
    print(format_incident_report(report))
    return 0


def sync_report(config: dict, *, limit: int) -> int:
    report, _state = build_stateful_report(config)
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    benchmark_summary = build_benchmark_summary(benchmark_path, limit=limit)
    print(format_sync_report(report, benchmark_summary))
    return 0 if report["status"] == "ok" else 1


def benchmark_item(report: dict[str, Any]) -> dict[str, Any]:
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
    latest_processed = progress.get("latest_processed") or {}
    disk = report.get("disk") or {}
    return {
        "checked_at": report["checked_at"],
        "node_name": report["node_name"],
        "status": report["status"],
        "severity": report["severity"],
        "failed_checks": failed_check_names(report),
        "peer_count": grpc_metrics.get("peer_count"),
        "active_peers": grpc_metrics.get("active_peers"),
        "is_synced": grpc_metrics.get("is_synced"),
        "network_id": grpc_metrics.get("network_id"),
        "virtual_daa_score": grpc_metrics.get("virtual_daa_score"),
        "block_count": grpc_metrics.get("block_count"),
        "header_count": grpc_metrics.get("header_count"),
        "mempool_size": grpc_metrics.get("mempool_size"),
        "tip_count": grpc_metrics.get("tip_count"),
        "virtual_parent_count": grpc_metrics.get("virtual_parent_count"),
        "difficulty": grpc_metrics.get("difficulty"),
        "network_hashes_per_second": grpc_metrics.get("network_hashes_per_second"),
        "pruning_point_hash": grpc_metrics.get("pruning_point_hash"),
        "outbound_peer_count": grpc_metrics.get("outbound_peer_count"),
        "inbound_peer_count": grpc_metrics.get("inbound_peer_count"),
        "process_resident_set_gib": (grpc_metrics.get("process") or {}).get("resident_set_gib"),
        "process_cpu_usage": (grpc_metrics.get("process") or {}).get("cpu_usage"),
        "process_fd_num": (grpc_metrics.get("process") or {}).get("fd_num"),
        "relay_blocks_in_window": progress.get("relay_blocks_in_window"),
        "relay_events_in_window": progress.get("relay_events_in_window"),
        "progress_window_minutes": progress.get("window_minutes"),
        "latest_relay_age_seconds": progress.get("latest_relay_age_seconds"),
        "latest_processed_age_seconds": progress.get("latest_processed_age_seconds"),
        "latest_processed_transactions_per_second": latest_processed.get("transactions_per_second"),
        "latest_processed_transactions": latest_processed.get("transactions"),
        "latest_processed_blocks": latest_processed.get("blocks"),
        "latest_processed_seconds": latest_processed.get("seconds"),
        "disk_free_gb": disk.get("free_gb"),
        "disk_free_percent": disk.get("free_percent"),
    }


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True))
        handle.write("\n")


def positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def save_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, sort_keys=True))
            handle.write("\n")


def prune_jsonl(path: Path, limit: int) -> int:
    items = load_jsonl(path)
    if len(items) <= limit:
        return 0
    pruned = len(items) - limit
    save_jsonl(path, items[-limit:])
    return pruned


def format_benchmark_snapshot(item: dict[str, Any], path: Path) -> str:
    failed = ", ".join(item.get("failed_checks") or []) or "none"
    lines = [
        f"Benchmark snapshot saved: {path}",
        f"node={item.get('node_name')} checked_at={item.get('checked_at')}",
        f"status={item.get('status')} severity={item.get('severity')} failed_checks={failed}",
        (
            "grpc="
            f"synced={item.get('is_synced')} "
            f"peers={item.get('peer_count')} "
            f"active={item.get('active_peers')} "
            f"network={item.get('network_id')} "
            f"daa={item.get('virtual_daa_score')} "
            f"blocks={item.get('block_count')}"
        ),
        (
            "progress="
            f"{item.get('relay_blocks_in_window')} relay blocks / "
            f"{item.get('relay_events_in_window')} events in "
            f"{item.get('progress_window_minutes')}m"
        ),
        (
            "processed="
            f"tx_rate={item.get('latest_processed_transactions_per_second')} "
            f"age={item.get('latest_processed_age_seconds')}s"
        ),
        f"disk_free={item.get('disk_free_gb')} GiB ({item.get('disk_free_percent')}%)",
    ]
    return "\n".join(lines)


def benchmark_snapshot(config: dict) -> int:
    report = build_report(config)
    path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    item = benchmark_item(report)
    append_jsonl(path, item)
    benchmark_limit = positive_int((config.get("retention") or {}).get("benchmark_entries"), 1000)
    pruned = prune_jsonl(path, benchmark_limit)
    print(format_benchmark_snapshot(item, path))
    if pruned:
        print(f"Benchmark retention pruned {pruned} old snapshots; limit={benchmark_limit}")
    return 0 if report["status"] == "ok" else 1


def format_rate(delta: float | None, hours: float) -> str:
    if delta is None or hours <= 0:
        return "unknown"
    return f"{delta / hours:.2f}/h"


def build_benchmark_summary(path: Path, *, limit: int) -> dict[str, Any]:
    items = load_jsonl(path)
    if limit > 0:
        items = items[-limit:]
    if items:
        latest = items[-1]
        latest_node = latest.get("node_name")
        latest_network = latest.get("network_id")
        items = [
            item
            for item in items
            if (not latest_node or item.get("node_name") == latest_node)
            and (not latest_network or item.get("network_id") == latest_network)
        ]
    if len(items) < 2:
        return {
            "ok": False,
            "path": str(path),
            "snapshots": len(items),
            "window": "need at least 2 snapshots",
            "daa_delta": "unknown",
            "daa_rate": "unknown",
            "daa_rate_per_hour": None,
            "block_delta": "unknown",
            "block_rate": "unknown",
            "block_rate_per_hour": None,
            "relay_rate": "unknown",
            "relay_rate_per_min": None,
            "latest_peer_state": "unknown",
            "latest_status": "unknown",
            "latest_severity": "unknown",
            "severity_counts": "{}",
            "ok_snapshots": 0,
            "warn_snapshots": 0,
            "critical_snapshots": 0,
            "ok_ratio": None,
            "min_peer_count": None,
            "min_disk_free_gb": None,
            "disk_delta": "unknown",
            "disk_delta_gb": None,
            "window_hours": None,
        }

    first = items[0]
    last = items[-1]
    first_at = parse_iso_datetime(first.get("checked_at"))
    last_at = parse_iso_datetime(last.get("checked_at"))
    if first_at is None or last_at is None:
        return {
            "ok": False,
            "path": str(path),
            "snapshots": len(items),
            "window": "invalid timestamps",
            "daa_delta": "unknown",
            "daa_rate": "unknown",
            "daa_rate_per_hour": None,
            "block_delta": "unknown",
            "block_rate": "unknown",
            "block_rate_per_hour": None,
            "relay_rate": "unknown",
            "relay_rate_per_min": None,
            "latest_peer_state": "unknown",
            "latest_status": "unknown",
            "latest_severity": "unknown",
            "severity_counts": "{}",
            "ok_snapshots": 0,
            "warn_snapshots": 0,
            "critical_snapshots": 0,
            "ok_ratio": None,
            "min_peer_count": None,
            "min_disk_free_gb": None,
            "disk_delta": "unknown",
            "disk_delta_gb": None,
            "window_hours": None,
        }

    elapsed_seconds = max(0.0, (last_at - first_at).total_seconds())
    elapsed_hours = elapsed_seconds / 3600
    first_daa = numeric(first.get("virtual_daa_score"))
    last_daa = numeric(last.get("virtual_daa_score"))
    first_blocks = numeric(first.get("block_count"))
    last_blocks = numeric(last.get("block_count"))
    first_disk = numeric(first.get("disk_free_gb"))
    last_disk = numeric(last.get("disk_free_gb"))
    daa_delta = None if first_daa is None or last_daa is None else last_daa - first_daa
    block_delta = None if first_blocks is None or last_blocks is None else last_blocks - first_blocks
    disk_delta = None if first_disk is None or last_disk is None else last_disk - first_disk
    relay_blocks = sum(int(item.get("relay_blocks_in_window") or 0) for item in items)
    relay_minutes = sum(float(item.get("progress_window_minutes") or 0) for item in items)
    relay_rate = "unknown" if relay_minutes <= 0 else f"{relay_blocks / relay_minutes:.2f}/min"
    severities: dict[str, int] = {}
    for item in items:
        severity = str(item.get("severity") or "unknown")
        severities[severity] = severities.get(severity, 0) + 1
    peer_counts = [value for value in (numeric(item.get("peer_count")) for item in items) if value is not None]
    disk_free_values = [
        value for value in (numeric(item.get("disk_free_gb")) for item in items) if value is not None
    ]
    ok_snapshots = severities.get("ok", 0)
    warn_snapshots = severities.get("warn", 0)
    critical_snapshots = severities.get("critical", 0)

    disk_delta_text = "unknown" if disk_delta is None else f"{disk_delta:+.2f} GiB"
    daa_rate_per_hour = None if daa_delta is None or elapsed_hours <= 0 else daa_delta / elapsed_hours
    block_rate_per_hour = (
        None if block_delta is None or elapsed_hours <= 0 else block_delta / elapsed_hours
    )
    relay_rate_per_min = None if relay_minutes <= 0 else relay_blocks / relay_minutes
    latest_peer_state = (
        f"peers={last.get('peer_count')} "
        f"active={last.get('active_peers')} "
        f"synced={last.get('is_synced')}"
    )
    return {
        "ok": True,
        "path": str(path),
        "snapshots": len(items),
        "window": f"{first.get('checked_at')} -> {last.get('checked_at')} ({elapsed_hours:.2f}h)",
        "daa_delta": daa_delta if daa_delta is not None else "unknown",
        "daa_rate": format_rate(daa_delta, elapsed_hours),
        "daa_rate_per_hour": daa_rate_per_hour,
        "block_delta": block_delta if block_delta is not None else "unknown",
        "block_rate": format_rate(block_delta, elapsed_hours),
        "block_rate_per_hour": block_rate_per_hour,
        "relay_rate": relay_rate,
        "relay_rate_per_min": relay_rate_per_min,
        "latest_peer_state": latest_peer_state,
        "latest_status": last.get("status"),
        "latest_severity": last.get("severity"),
        "severity_counts": json.dumps(severities, sort_keys=True),
        "ok_snapshots": ok_snapshots,
        "warn_snapshots": warn_snapshots,
        "critical_snapshots": critical_snapshots,
        "ok_ratio": ok_snapshots / len(items),
        "min_peer_count": min(peer_counts) if peer_counts else None,
        "min_disk_free_gb": min(disk_free_values) if disk_free_values else None,
        "disk_delta": disk_delta_text,
        "disk_delta_gb": disk_delta,
        "window_hours": elapsed_hours,
    }


def benchmark_report(config: dict, *, limit: int) -> int:
    path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    summary = build_benchmark_summary(path, limit=limit)
    if not summary["ok"]:
        print(f"Benchmark report unavailable: {summary['window']} in {path}")
        return 2
    lines = [
        f"Kaspa benchmark report: {config['node_name']}",
        f"snapshots={summary['snapshots']} path={path}",
        f"window={summary['window']}",
        f"daa_delta={summary['daa_delta']} rate={summary['daa_rate']}",
        f"block_count_delta={summary['block_delta']} rate={summary['block_rate']}",
        f"relay_window_average={summary['relay_rate']}",
        f"latest_{summary['latest_peer_state']}",
        f"latest_status={summary['latest_status']} severity={summary['latest_severity']}",
        f"severity_counts={summary['severity_counts']}",
        f"ok_ratio={format_ratio(summary.get('ok_ratio'))}",
        f"min_peer_count={format_optional_number(summary.get('min_peer_count'))}",
        f"min_disk_free={format_gib(summary.get('min_disk_free_gb'))}",
        f"disk_free_delta={summary['disk_delta']}",
    ]
    print("\n".join(lines))
    return 0 if summary["latest_status"] == "ok" else 1


def prometheus_label_value(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def prometheus_labels(labels: dict[str, Any]) -> str:
    if not labels:
        return ""
    content = ",".join(
        f'{key}="{prometheus_label_value(value)}"' for key, value in sorted(labels.items())
    )
    return "{" + content + "}"


def prometheus_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return numeric(value)


def add_prometheus_metric(
    lines: list[str],
    name: str,
    value: Any,
    labels: dict[str, Any],
) -> None:
    number = prometheus_number(value)
    if number is None:
        return
    lines.append(f"{name}{prometheus_labels(labels)} {number:g}")


def iso_timestamp_seconds(value: Any) -> float | None:
    parsed = parse_iso_datetime(value)
    return parsed.timestamp() if parsed else None


def build_recovery_summary(path: Path) -> dict[str, Any]:
    try:
        records = load_jsonl(path)
    except (OSError, json.JSONDecodeError):
        records = []
    executed = [item for item in records if item.get("action") == "executed"]
    dry_runs = [item for item in records if item.get("action") == "dry_run"]
    skipped = [item for item in records if item.get("action") == "skipped"]
    unavailable = [item for item in records if item.get("action") == "unavailable"]
    last = records[-1] if records else {}
    return {
        "attempts": len(records),
        "executed": len(executed),
        "dry_runs": len(dry_runs),
        "skipped": len(skipped),
        "unavailable": len(unavailable),
        "last_started_at": last.get("started_at"),
        "last_completed_at": last.get("completed_at"),
        "last_exit_code": last.get("exit_code"),
    }


def format_prometheus_metrics(
    report: dict[str, Any],
    benchmark_summary: dict[str, Any],
    recovery_summary: dict[str, Any] | None = None,
) -> str:
    node_labels = {"node": report["node_name"]}
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
    latest_processed = progress.get("latest_processed") or {}
    sync_progress = report.get("sync_progress") or {}
    monitoring = report.get("monitoring") or {}
    disk = report.get("disk") or {}
    recovery_summary = recovery_summary or {}
    severity_values = {"ok": 0, "warn": 1, "critical": 2}
    lines = [
        "# TYPE kaspa_watchtower_status_ok gauge",
        "# TYPE kaspa_watchtower_severity_value gauge",
        "# TYPE kaspa_watchtower_check_ok gauge",
    ]
    add_prometheus_metric(lines, "kaspa_watchtower_status_ok", report["status"] == "ok", node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_severity_value",
        severity_values.get(report["severity"], -1),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_failed_checks",
        len(failed_check_names(report)),
        node_labels,
    )
    for check in report["checks"]:
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_check_ok",
            check.get("ok"),
            {**node_labels, "check": check.get("name", "unknown")},
        )

    add_prometheus_metric(lines, "kaspa_watchtower_peer_count", grpc_metrics.get("peer_count"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_outbound_peer_count",
        grpc_metrics.get("outbound_peer_count"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_inbound_peer_count",
        grpc_metrics.get("inbound_peer_count"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_active_peer_count",
        grpc_metrics.get("active_peers"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_synced", grpc_metrics.get("is_synced"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_virtual_daa_score",
        grpc_metrics.get("virtual_daa_score"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_block_count", grpc_metrics.get("block_count"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_header_count", grpc_metrics.get("header_count"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sync_active", sync_progress.get("active"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_baseline_available",
        sync_progress.get("baseline_available"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_elapsed_minutes",
        sync_progress.get("elapsed_minutes"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_sync_daa_delta", sync_progress.get("daa_delta"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sync_block_delta", sync_progress.get("block_delta"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sync_header_delta", sync_progress.get("header_delta"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sync_daa_rate_per_hour", sync_progress.get("daa_rate_per_hour"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sync_block_rate_per_hour", sync_progress.get("block_rate_per_hour"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sync_header_rate_per_hour", sync_progress.get("header_rate_per_hour"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_require_synced", monitoring.get("require_synced"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_require_relay_progress_when_unsynced",
        monitoring.get("require_relay_progress_when_unsynced"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_require_sync_progress_when_unsynced",
        monitoring.get("require_sync_progress_when_unsynced"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_progress_stall_minutes",
        monitoring.get("sync_progress_stall_minutes"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_mempool_size", grpc_metrics.get("mempool_size"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_tip_count", grpc_metrics.get("tip_count"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_blocks",
        latest_processed.get("blocks"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_headers",
        latest_processed.get("headers"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_transactions",
        latest_processed.get("transactions"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_seconds",
        latest_processed.get("seconds"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_blocks_per_second",
        latest_processed.get("blocks_per_second"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_headers_per_second",
        latest_processed.get("headers_per_second"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_transactions_per_second",
        latest_processed.get("transactions_per_second"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_timestamp_seconds",
        iso_timestamp_seconds(latest_processed.get("timestamp")),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_processed_age_seconds",
        progress.get("latest_processed_age_seconds"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_virtual_parent_count",
        grpc_metrics.get("virtual_parent_count"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_difficulty", grpc_metrics.get("difficulty"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_network_hashes_per_second",
        grpc_metrics.get("network_hashes_per_second"),
        node_labels,
    )
    process = grpc_metrics.get("process") or {}
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_process_resident_set_gib",
        process.get("resident_set_gib"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_process_cpu_usage",
        process.get("cpu_usage"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_process_fd_num",
        process.get("fd_num"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_relay_blocks_window",
        progress.get("relay_blocks_in_window"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_relay_events_window",
        progress.get("relay_events_in_window"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_latest_relay_age_seconds",
        progress.get("latest_relay_age_seconds"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_disk_free_gb", disk.get("free_gb"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_disk_free_percent",
        disk.get("free_percent"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_snapshots",
        benchmark_summary.get("snapshots"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_window_hours",
        benchmark_summary.get("window_hours"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_daa_rate_per_hour",
        benchmark_summary.get("daa_rate_per_hour"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_block_rate_per_hour",
        benchmark_summary.get("block_rate_per_hour"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_relay_rate_per_min",
        benchmark_summary.get("relay_rate_per_min"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_ok_snapshots",
        benchmark_summary.get("ok_snapshots"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_warn_snapshots",
        benchmark_summary.get("warn_snapshots"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_critical_snapshots",
        benchmark_summary.get("critical_snapshots"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_ok_ratio",
        benchmark_summary.get("ok_ratio"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_min_peer_count",
        benchmark_summary.get("min_peer_count"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_min_disk_free_gb",
        benchmark_summary.get("min_disk_free_gb"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_benchmark_disk_free_delta_gb",
        benchmark_summary.get("disk_delta_gb"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_attempts_total",
        recovery_summary.get("attempts"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_executed_total",
        recovery_summary.get("executed"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_dry_runs_total",
        recovery_summary.get("dry_runs"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_skipped_total",
        recovery_summary.get("skipped"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_unavailable_total",
        recovery_summary.get("unavailable"),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_last_started_timestamp_seconds",
        iso_timestamp_seconds(recovery_summary.get("last_started_at")),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_last_completed_timestamp_seconds",
        iso_timestamp_seconds(recovery_summary.get("last_completed_at")),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_recovery_last_exit_code",
        recovery_summary.get("last_exit_code"),
        node_labels,
    )
    return "\n".join(lines) + "\n"


def write_prometheus_metrics(
    path: Path,
    report: dict[str, Any],
    benchmark_summary: dict[str, Any],
    recovery_summary: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        format_prometheus_metrics(report, benchmark_summary, recovery_summary),
        encoding="utf-8",
    )


def prometheus(config: dict) -> int:
    report, _state = build_stateful_report(config)
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    metrics_path = Path(config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"])
    benchmark_summary = build_benchmark_summary(benchmark_path, limit=48)
    recovery_summary = build_recovery_summary(recovery_history_path(config))
    write_prometheus_metrics(metrics_path, report, benchmark_summary, recovery_summary)
    print(f"Prometheus metrics written: {metrics_path}")
    return 0 if report["status"] == "ok" else 1


def endpoint_configured(endpoint: str) -> bool:
    if not endpoint or ":" not in endpoint:
        return False
    host, port_text = endpoint.rsplit(":", 1)
    if not host:
        return False
    try:
        port = int(port_text)
    except ValueError:
        return False
    return 0 < port < 65536


def path_parent_writable(path: str) -> bool:
    if not path:
        return False
    parent = Path(path).expanduser().parent
    return parent.exists() and os.access(parent, os.W_OK)


def nested_config_value(config: dict, section: str, key: str) -> Any:
    section_config = config.get(section) or {}
    if not isinstance(section_config, dict):
        section_config = {}
    default_section = DEFAULT_CONFIG.get(section) or {}
    return section_config.get(key, default_section.get(key))


def positive_int_config(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def non_negative_int_config(value: Any) -> bool:
    try:
        return int(value) >= 0
    except (TypeError, ValueError):
        return False


def number_between_config(value: Any, minimum: float, maximum: float | None = None) -> bool:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    if parsed < minimum:
        return False
    if maximum is not None and parsed > maximum:
        return False
    return True


def validation_detail(value: Any, expected: str, ok: bool) -> str:
    status = "ok" if ok else f"expected {expected}"
    return f"{value if value not in (None, '') else 'missing'}; {status}"


def path_exists_check(name: str, value: Any, expected: str = "existing path") -> Check:
    text = str(value or "")
    ok = bool(text) and Path(text).exists()
    return Check(name, ok, validation_detail(text, expected, ok))


def parent_writable_check(name: str, value: Any) -> Check:
    text = str(value or "")
    ok = path_parent_writable(text)
    return Check(name, ok, validation_detail(text, "writable parent directory", ok))


def endpoint_check(name: str, value: Any) -> Check:
    text = str(value or "")
    ok = endpoint_configured(text)
    return Check(name, ok, validation_detail(text, "host:port endpoint", ok))


def config_validation_checks(config: dict) -> list[Check]:
    recovery = config.get("recovery") or {}
    if not isinstance(recovery, dict):
        recovery = {}
    restart_command = recovery.get("restart_command") or []
    recovery_mode = recovery.get("mode", DEFAULT_CONFIG["recovery"]["mode"])
    config_version = config.get("config_version", DEFAULT_CONFIG["config_version"])
    config_version_ok = isinstance(config_version, int) and 1 <= config_version <= DEFAULT_CONFIG["config_version"]
    checks = [
        Check(
            "config_version",
            config_version_ok,
            validation_detail(
                config_version,
                f"integer between 1 and {DEFAULT_CONFIG['config_version']}",
                config_version_ok,
            ),
        ),
        Check(
            "node_name",
            bool(config.get("node_name")),
            validation_detail(config.get("node_name"), "non-empty node name", bool(config.get("node_name"))),
        ),
        Check(
            "process_match",
            bool(config.get("process_match")),
            validation_detail(
                config.get("process_match"),
                "non-empty process name fragment",
                bool(config.get("process_match")),
            ),
        ),
        endpoint_check("rpc_endpoint", config.get("rpc_endpoint")),
        endpoint_check("grpc_endpoint", config.get("grpc_endpoint")),
        path_exists_check("log_path", config.get("log_path"), "existing kaspad log file"),
        path_exists_check("data_dir", config.get("data_dir"), "existing kaspad data directory"),
        parent_writable_check("state_path", config.get("state_path") or DEFAULT_CONFIG["state_path"]),
        parent_writable_check(
            "status_page_path",
            config.get("status_page_path") or DEFAULT_CONFIG["status_page_path"],
        ),
        parent_writable_check("benchmark_path", config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"]),
        parent_writable_check(
            "prometheus_metrics_path",
            config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"],
        ),
        parent_writable_check(
            "recovery_history_path",
            config.get("recovery_history_path") or DEFAULT_CONFIG["recovery_history_path"],
        ),
        Check(
            "recovery.mode",
            recovery_mode in {"manual"},
            validation_detail(recovery_mode, "manual", recovery_mode in {"manual"}),
        ),
        Check(
            "recovery.post_recovery_wait_seconds",
            number_between_config(recovery.get("post_recovery_wait_seconds", 20), 0),
            validation_detail(
                recovery.get("post_recovery_wait_seconds", 20),
                "number >= 0",
                number_between_config(recovery.get("post_recovery_wait_seconds", 20), 0),
            ),
        ),
    ]
    threshold_specs = [
        ("alert_repeat_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("stale_log_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("stale_processed_stats_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("progress_window_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("min_relay_blocks_in_window", non_negative_int_config, "integer >= 0"),
        ("min_peer_count", non_negative_int_config, "integer >= 0"),
        ("disk_free_gb_min", lambda value: number_between_config(value, 0), "number >= 0"),
        ("disk_free_percent_min", lambda value: number_between_config(value, 0, 100), "number between 0 and 100"),
        ("require_rpc", lambda value: isinstance(value, bool), "boolean"),
        ("require_grpc_metrics", lambda value: isinstance(value, bool), "boolean"),
        ("require_synced", lambda value: isinstance(value, bool), "boolean"),
        ("require_relay_progress_when_unsynced", lambda value: isinstance(value, bool), "boolean"),
        ("require_sync_progress_when_unsynced", lambda value: isinstance(value, bool), "boolean"),
        ("sync_progress_stall_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("min_sync_daa_delta", non_negative_int_config, "integer >= 0"),
        ("min_sync_block_delta", non_negative_int_config, "integer >= 0"),
        ("min_sync_header_delta", non_negative_int_config, "integer >= 0"),
    ]
    for key, validator, expected in threshold_specs:
        value = nested_config_value(config, "thresholds", key)
        ok = validator(value)
        checks.append(Check(f"thresholds.{key}", ok, validation_detail(value, expected, ok)))

    retention_specs = [
        ("state_history_entries", positive_int_config, "integer > 0"),
        ("benchmark_entries", positive_int_config, "integer > 0"),
    ]
    for key, validator, expected in retention_specs:
        value = nested_config_value(config, "retention", key)
        ok = validator(value)
        checks.append(Check(f"retention.{key}", ok, validation_detail(value, expected, ok)))
    canvas_status_page = config.get("canvas_status_page_path") or ""
    if canvas_status_page:
        checks.append(parent_writable_check("canvas_status_page_path", canvas_status_page))
    if restart_command:
        ok = bool(shutil.which(str(restart_command[0])))
        checks.append(
            Check(
                "recovery.restart_command",
                ok,
                validation_detail(
                    " ".join(str(part) for part in restart_command),
                    "executable command on PATH",
                    ok,
                ),
            )
        )
    return checks


def validate_config(config: dict) -> int:
    checks = config_validation_checks(config)
    failed = [check for check in checks if not check.ok]
    print(f"Config validation: {config.get('node_name') or 'unknown'}")
    for check in checks:
        mark = "OK" if check.ok else "FAIL"
        print(f"{mark} {check.name}: {check.detail}")
    if failed:
        names = ", ".join(check.name for check in failed)
        print(f"Config validation failed: {len(failed)} issue(s): {names}")
        return 1
    print("Config validation passed")
    return 0


def prune_state(config: dict) -> int:
    state_path = Path(config.get("state_path") or DEFAULT_CONFIG["state_path"])
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    retention = config.get("retention") or {}
    history_limit = positive_int(retention.get("state_history_entries"), 100)
    benchmark_limit = positive_int(retention.get("benchmark_entries"), 1000)

    state = load_state(state_path)
    history = state.get("history", [])
    pruned_history = max(0, len(history) - history_limit)
    if pruned_history:
        state["history"] = history[-history_limit:]
        save_state(state_path, state)

    pruned_benchmarks = prune_jsonl(benchmark_path, benchmark_limit)
    print(f"State retention: history_pruned={pruned_history} limit={history_limit} path={state_path}")
    print(f"Benchmark retention: snapshots_pruned={pruned_benchmarks} limit={benchmark_limit} path={benchmark_path}")
    return 0


def recovery_history_path(config: dict) -> Path:
    return Path(config.get("recovery_history_path") or DEFAULT_CONFIG["recovery_history_path"])


def append_recovery_record(config: dict, record: dict[str, Any]) -> Path:
    path = recovery_history_path(config)
    append_jsonl(path, record)
    return path


def report_recovery_record(config: dict, record: dict[str, Any]) -> None:
    path = append_recovery_record(config, record)
    print(f"Recovery record written: {path}")


def format_recovery_decision(
    report: dict[str, Any],
    *,
    mode: str,
    restart_command: list[str],
    force: bool,
    dry_run: bool,
) -> str:
    failed = failed_check_names(report)
    failed_text = ",".join(failed) if failed else "none"
    configured = bool(restart_command)
    if not configured:
        next_action = "configure recovery.restart_command before recovery"
    elif mode != "manual":
        next_action = f"unsupported recovery mode {mode}"
    elif report.get("severity") == "ok" and not force:
        next_action = "skip recovery; node is healthy"
    elif dry_run:
        next_action = "review command and rerun without --dry-run only after approval"
    else:
        next_action = "execute configured restart command"

    return "\n".join(
        [
            "Recovery decision:",
            (
                f"  node={report.get('node_name', 'unknown')} "
                f"status={report.get('status', 'unknown')} "
                f"severity={report.get('severity', 'unknown')}"
            ),
            f"  failed_checks={failed_text}",
            f"  mode={mode} force={force} dry_run={dry_run}",
            f"  restart_command_configured={configured}",
            f"  next={next_action}",
        ]
    )


def recover(config: dict, *, force: bool = False, dry_run: bool = False) -> int:
    report = build_report(config)
    recovery = config.get("recovery", {})
    restart_command = recovery.get("restart_command") or []
    mode = recovery.get("mode", "manual")
    record: dict[str, Any] = {
        "started_at": dt.datetime.now().astimezone().isoformat(),
        "node_name": report.get("node_name"),
        "status_before": report.get("status"),
        "severity_before": report.get("severity"),
        "failed_checks_before": failed_check_names(report),
        "mode": mode,
        "force": force,
        "dry_run": dry_run,
        "restart_command": restart_command,
    }
    print(
        format_recovery_decision(
            report,
            mode=mode,
            restart_command=restart_command,
            force=force,
            dry_run=dry_run,
        )
    )

    if not restart_command:
        record.update({"action": "unavailable", "reason": "restart_command is not configured"})
        print("Recovery unavailable: restart_command is not configured")
        report_recovery_record(config, record)
        return 2
    if mode != "manual":
        record.update({"action": "unavailable", "reason": f"unsupported mode={mode}"})
        print(f"Recovery unavailable: unsupported mode={mode}")
        report_recovery_record(config, record)
        return 2
    if report["severity"] == "ok" and not force:
        record.update({"action": "skipped", "reason": "node is healthy"})
        print("Recovery skipped: node is healthy; use --force-recover to override")
        report_recovery_record(config, record)
        return 0

    print(f"Recovery target: {report['node_name']} severity={report['severity']}")
    print("Recovery command: " + " ".join(restart_command))
    if dry_run:
        record.update({"action": "dry_run", "completed_at": dt.datetime.now().astimezone().isoformat()})
        print("Recovery dry-run: command not executed")
        report_recovery_record(config, record)
        return 0

    record["action"] = "executed"
    completed = run_command_result(restart_command)
    record["exit_code"] = completed.returncode
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip())
    if completed.returncode == 0:
        print("Recovery command completed")
    else:
        print(f"Recovery command failed with exit code {completed.returncode}")
        record["completed_at"] = dt.datetime.now().astimezone().isoformat()
        report_recovery_record(config, record)
        return completed.returncode

    wait_seconds = float(recovery.get("post_recovery_wait_seconds", 20))
    if wait_seconds > 0:
        print(f"Post-recovery check waiting {wait_seconds:g}s")
        time.sleep(wait_seconds)
    after = build_report(config)
    record.update(
        {
            "completed_at": dt.datetime.now().astimezone().isoformat(),
            "status_after": after.get("status"),
            "severity_after": after.get("severity"),
            "failed_checks_after": failed_check_names(after),
        }
    )
    print(f"Post-recovery status: {after['status']} severity={after['severity']}")
    report_recovery_record(config, record)
    if after["status"] != "ok":
        return 1
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Report local Kaspa node health.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("-c", "--config", type=Path, help="Path to config JSON.")
    parser.add_argument("--json", action="store_true", help="Print a JSON health report.")
    parser.add_argument("--summary", action="store_true", help="Print a concise text health summary.")
    parser.add_argument("--sync-report", action="store_true", help="Print a focused mainnet sync progress report.")
    parser.add_argument("--diagnostics-summary", action="store_true", help="Print a sanitized diagnostics summary.")
    parser.add_argument("--incident-report", action="store_true", help="Print a sanitized Markdown incident report.")
    parser.add_argument("--alert", action="store_true", help="Print only alert transition output and update state.")
    parser.add_argument("--recover", action="store_true", help="Run the configured manual recovery command when unhealthy.")
    parser.add_argument("--force-recover", action="store_true", help="Run recovery even when the current report is healthy.")
    parser.add_argument("--dry-run", action="store_true", help="Show recovery command without executing it.")
    parser.add_argument("--benchmark-snapshot", action="store_true", help="Append a benchmark snapshot to the JSONL benchmark log.")
    parser.add_argument("--benchmark-report", action="store_true", help="Print a benchmark report from saved snapshots.")
    parser.add_argument("--benchmark-limit", type=int, default=100, help="Number of recent benchmark snapshots to include.")
    parser.add_argument("--prometheus", action="store_true", help="Write Prometheus textfile metrics.")
    parser.add_argument("--validate-config", action="store_true", help="Validate config paths, endpoints, and commands.")
    parser.add_argument("--prune-state", action="store_true", help="Apply configured retention limits to local state files.")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.json:
        report, _state = build_stateful_report(config)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "ok" else 1
    if args.summary:
        report, _state = build_stateful_report(config)
        print(format_summary(report))
        return 0 if report["status"] == "ok" else 1
    if args.sync_report:
        return sync_report(config, limit=args.benchmark_limit)
    if args.diagnostics_summary:
        return diagnostics_summary(config)
    if args.incident_report:
        return incident_report(config)
    if args.alert:
        return alert(config)
    if args.recover:
        return recover(config, force=args.force_recover, dry_run=args.dry_run)
    if args.benchmark_snapshot:
        return benchmark_snapshot(config)
    if args.benchmark_report:
        return benchmark_report(config, limit=args.benchmark_limit)
    if args.prometheus:
        return prometheus(config)
    if args.validate_config:
        return validate_config(config)
    if args.prune_state:
        return prune_state(config)
    return status(config)


if __name__ == "__main__":
    raise SystemExit(main())
