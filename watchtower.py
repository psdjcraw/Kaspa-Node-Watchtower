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


VERSION = "0.2.0-dev"

DEFAULT_CONFIG = {
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
                latest_processed = processed_stats[-1]
                progress["latest_processed"] = {
                    "timestamp": latest_processed.timestamp.isoformat(),
                    "blocks": latest_processed.blocks,
                    "headers": latest_processed.headers,
                    "seconds": latest_processed.seconds,
                    "transactions": latest_processed.transactions,
                    "line": latest_processed.line,
                }
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


def short_hash(value: Any, length: int = 12) -> str:
    text = str(value or "")
    return text[:length] if text else "unknown"


def history_item(report: dict[str, Any]) -> dict[str, Any]:
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
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
        "relay_blocks_in_window": progress.get("relay_blocks_in_window"),
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
    metric_items = [
        ("Severity", report["severity"]),
        ("Peers", grpc_metrics.get("peer_count", "unknown")),
        ("Active Peers", grpc_metrics.get("active_peers", "unknown")),
        ("Outbound Peers", grpc_metrics.get("outbound_peer_count", "unknown")),
        ("Inbound Peers", grpc_metrics.get("inbound_peer_count", "unknown")),
        ("Synced", grpc_metrics.get("is_synced", "unknown")),
        ("Network", grpc_metrics.get("network_id", "unknown")),
        ("DAA Score", grpc_metrics.get("virtual_daa_score", "unknown")),
        ("Block Count", grpc_metrics.get("block_count", "unknown")),
        ("Header Count", grpc_metrics.get("header_count", "unknown")),
        ("Sync Baseline", sync_progress.get("baseline_checked_at", "pending")),
        ("Sync Header Rate", format_optional_rate(sync_progress.get("header_rate_per_hour"))),
        ("Sync DAA Delta", format_delta(numeric(sync_progress.get("daa_delta")))),
        ("Sync Block Delta", format_delta(numeric(sync_progress.get("block_delta")))),
        ("Sync Header Delta", format_delta(numeric(sync_progress.get("header_delta")))),
        ("Mempool", grpc_metrics.get("mempool_size", "unknown")),
        ("Tips", grpc_metrics.get("tip_count", "unknown")),
        ("Virtual Parents", grpc_metrics.get("virtual_parent_count", "unknown")),
        ("Difficulty", grpc_metrics.get("difficulty", "unknown")),
        ("Pruning Point", short_hash(grpc_metrics.get("pruning_point_hash"))),
        ("Relay Blocks", progress.get("relay_blocks_in_window", 0)),
        ("Disk Free", f"{report.get('disk', {}).get('free_gb', 'unknown')} GiB"),
        ("Recovery", recovery.get("action", "none")),
    ]
    metrics_html = "\n".join(
        f"""<section class="metric">
  <div class="metric-label">{html.escape(str(label))}</div>
  <div class="metric-value">{html.escape(str(value))}</div>
</section>"""
        for label, value in metric_items
    )
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
      --muted: #65758b;
      --line: #d7dde5;
      --panel: #ffffff;
      --page: #f6f8fb;
      --ok: #137333;
      --warn: #b26a00;
      --critical: #b3261e;
      --accent: #3858e9;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 18px;
    }}
    h1 {{ font-size: 24px; margin: 0 0 6px; line-height: 1.2; }}
    h2 {{ font-size: 16px; margin: 0 0 10px; }}
    .subtle {{ color: var(--muted); font-size: 13px; }}
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
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-bottom: 18px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .metric-label {{ color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .metric-value {{ font-size: 18px; font-weight: 700; overflow-wrap: anywhere; }}
    .layout {{ display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 14px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    code {{ background: #eef2f6; padding: 2px 4px; border-radius: 4px; }}
    .ok-text {{ color: var(--ok); font-weight: 700; }}
    .fail-text {{ color: var(--critical); font-weight: 700; }}
    .notes {{ display: grid; gap: 8px; font-size: 13px; }}
    @media (max-width: 760px) {{
      main {{ padding: 14px; }}
      header, .layout {{ display: block; }}
      .badge {{ margin-top: 10px; }}
      .panel {{ margin-bottom: 12px; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Kaspa Node Watchtower</h1>
        <div class="subtle">{html.escape(report['node_name'])} · checked at <code>{html.escape(report['checked_at'])}</code> · auto-refresh 60s</div>
      </div>
      <div class="badge {html.escape(report['severity'])}">{html.escape(report['severity'])}</div>
    </header>
    <section class="metrics">
      {metrics_html}
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Checks</h2>
        <table>
          <thead>{html_row(["State", "Check", "Detail"], "th")}</thead>
          <tbody>{checks}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Run Context</h2>
        <div class="notes">
          <div>Failed checks: <code>{html.escape(failure_text)}</code></div>
          <div>Last alert: <code>{html.escape(str(last_alert_at))}</code></div>
          <div>Latest throughput: <code>{html.escape(str(report.get('latest_throughput') or 'unknown'))}</code></div>
          <div>Recovery mode: <code>{html.escape(str(recovery.get('mode', 'unknown')))}</code></div>
        </div>
      </section>
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
            f"tips={grpc_metrics.get('tip_count', 'unknown')}"
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


def sync_report(config: dict, *, limit: int) -> int:
    report, _state = build_stateful_report(config)
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    benchmark_summary = build_benchmark_summary(benchmark_path, limit=limit)
    print(format_sync_report(report, benchmark_summary))
    return 0 if report["status"] == "ok" else 1


def benchmark_item(report: dict[str, Any]) -> dict[str, Any]:
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
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
        "kaspa_watchtower_virtual_parent_count",
        grpc_metrics.get("virtual_parent_count"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_difficulty", grpc_metrics.get("difficulty"), node_labels)
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


def config_validation_checks(config: dict) -> list[Check]:
    recovery = config.get("recovery", {})
    restart_command = recovery.get("restart_command") or []
    recovery_mode = recovery.get("mode", DEFAULT_CONFIG["recovery"]["mode"])
    checks = [
        Check("node_name", bool(config.get("node_name")), str(config.get("node_name") or "missing")),
        Check("process_match", bool(config.get("process_match")), str(config.get("process_match") or "missing")),
        Check("rpc_endpoint", endpoint_configured(config.get("rpc_endpoint") or ""), config.get("rpc_endpoint") or "missing"),
        Check("grpc_endpoint", endpoint_configured(config.get("grpc_endpoint") or ""), config.get("grpc_endpoint") or "missing"),
        Check("log_path", Path(config.get("log_path") or "").exists(), config.get("log_path") or "missing"),
        Check("data_dir", Path(config.get("data_dir") or "").exists(), config.get("data_dir") or "missing"),
        Check("state_path", path_parent_writable(config.get("state_path") or DEFAULT_CONFIG["state_path"]), config.get("state_path") or DEFAULT_CONFIG["state_path"]),
        Check("status_page_path", path_parent_writable(config.get("status_page_path") or DEFAULT_CONFIG["status_page_path"]), config.get("status_page_path") or DEFAULT_CONFIG["status_page_path"]),
        Check("benchmark_path", path_parent_writable(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"]), config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"]),
        Check("prometheus_metrics_path", path_parent_writable(config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"]), config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"]),
        Check("recovery_history_path", path_parent_writable(config.get("recovery_history_path") or DEFAULT_CONFIG["recovery_history_path"]), config.get("recovery_history_path") or DEFAULT_CONFIG["recovery_history_path"]),
        Check("recovery.mode", recovery_mode in {"manual"}, str(recovery_mode)),
        Check(
            "recovery.post_recovery_wait_seconds",
            number_between_config(recovery.get("post_recovery_wait_seconds", 20), 0),
            str(recovery.get("post_recovery_wait_seconds", 20)),
        ),
    ]
    threshold_specs = [
        ("alert_repeat_minutes", lambda value: number_between_config(value, 1)),
        ("stale_log_minutes", lambda value: number_between_config(value, 1)),
        ("progress_window_minutes", lambda value: number_between_config(value, 1)),
        ("min_relay_blocks_in_window", non_negative_int_config),
        ("min_peer_count", non_negative_int_config),
        ("disk_free_gb_min", lambda value: number_between_config(value, 0)),
        ("disk_free_percent_min", lambda value: number_between_config(value, 0, 100)),
        ("require_rpc", lambda value: isinstance(value, bool)),
        ("require_grpc_metrics", lambda value: isinstance(value, bool)),
        ("require_synced", lambda value: isinstance(value, bool)),
        ("require_relay_progress_when_unsynced", lambda value: isinstance(value, bool)),
        ("require_sync_progress_when_unsynced", lambda value: isinstance(value, bool)),
        ("sync_progress_stall_minutes", lambda value: number_between_config(value, 1)),
        ("min_sync_daa_delta", non_negative_int_config),
        ("min_sync_block_delta", non_negative_int_config),
        ("min_sync_header_delta", non_negative_int_config),
    ]
    for key, validator in threshold_specs:
        value = nested_config_value(config, "thresholds", key)
        checks.append(Check(f"thresholds.{key}", validator(value), str(value)))

    retention_specs = [
        ("state_history_entries", positive_int_config),
        ("benchmark_entries", positive_int_config),
    ]
    for key, validator in retention_specs:
        value = nested_config_value(config, "retention", key)
        checks.append(Check(f"retention.{key}", validator(value), str(value)))
    canvas_status_page = config.get("canvas_status_page_path") or ""
    if canvas_status_page:
        checks.append(
            Check(
                "canvas_status_page_path",
                path_parent_writable(canvas_status_page),
                canvas_status_page,
            )
        )
    if restart_command:
        checks.append(
            Check(
                "recovery.restart_command",
                bool(shutil.which(str(restart_command[0]))),
                " ".join(str(part) for part in restart_command),
            )
        )
    return checks


def validate_config(config: dict) -> int:
    checks = config_validation_checks(config)
    print(f"Config validation: {config.get('node_name') or 'unknown'}")
    for check in checks:
        mark = "OK" if check.ok else "FAIL"
        print(f"{mark} {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


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
