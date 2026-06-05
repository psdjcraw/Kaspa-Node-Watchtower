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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


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
    "thresholds": {
        "alert_repeat_minutes": 60,
        "stale_log_minutes": 15,
        "progress_window_minutes": 10,
        "min_relay_blocks_in_window": 1,
        "min_peer_count": 1,
        "require_grpc_metrics": True,
        "disk_free_gb_min": 20,
        "disk_free_percent_min": 5,
        "require_rpc": True,
    },
    "recovery": {
        "mode": "manual",
        "restart_command": [],
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
        checks.append(
            Check(
                "sync_status",
                bool(grpc_metrics.get("is_synced")),
                f"is_synced={bool(grpc_metrics.get('is_synced'))}",
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
            checks.append(
                Check(
                    "block_progress",
                    recent_relay_blocks >= min_relay_blocks,
                    (
                        f"{recent_relay_blocks} relay blocks in "
                        f"{progress_window:g}m window "
                        f"({len(recent_relay_events)} events)"
                    ),
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
        "recovery": recovery_plan(config, severity),
    }
    return report


def status(config: dict) -> int:
    report = build_report(config)

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
            f"daa={grpc_metrics.get('virtual_daa_score')}"
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
        "virtual_daa_score": grpc_metrics.get("virtual_daa_score"),
        "relay_blocks_in_window": progress.get("relay_blocks_in_window"),
    }


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


def write_status_page(path: Path, report: dict[str, Any], state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checks = "\n".join(
        html_row([
            "OK" if check["ok"] else "ALERT",
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
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kaspa Node Watchtower</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2933; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin-top: 24px; }}
    .status {{ font-weight: 700; }}
    .ok {{ color: #137333; }}
    .warn {{ color: #b26a00; }}
    .critical {{ color: #b3261e; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #d7dde5; padding: 7px 8px; text-align: left; vertical-align: top; }}
    code {{ background: #eef2f6; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Kaspa Node Watchtower</h1>
  <p class="status {html.escape(report['severity'])}">{html.escape(report['node_name'])}: {html.escape(report['severity'])}</p>
  <p>Checked at: <code>{html.escape(report['checked_at'])}</code></p>
  <p>Network: <code>{html.escape(str(grpc_metrics.get('network_id', 'unknown')))}</code>,
     synced: <code>{html.escape(str(grpc_metrics.get('is_synced', 'unknown')))}</code>,
     peers: <code>{html.escape(str(grpc_metrics.get('peer_count', 'unknown')))}</code>,
     DAA: <code>{html.escape(str(grpc_metrics.get('virtual_daa_score', 'unknown')))}</code></p>
  <p>Relay progress: <code>{html.escape(str(progress.get('relay_blocks_in_window', 0)))}</code> blocks in
     <code>{html.escape(str(progress.get('window_minutes', 0)))}</code> minutes.</p>
  <h2>Checks</h2>
  <table>
    <thead>{html_row(["State", "Check", "Detail"], "th")}</thead>
    <tbody>{checks}</tbody>
  </table>
  <h2>Recent History</h2>
  <table>
    <thead>{html_row(["Checked At", "Severity", "Failed", "Peers", "Relay Blocks", "DAA"], "th")}</thead>
    <tbody>{history}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(page, encoding="utf-8")


def alert(config: dict) -> int:
    report = build_report(config)
    state_path = Path(config.get("state_path") or DEFAULT_CONFIG["state_path"])
    status_page_path = Path(config.get("status_page_path") or DEFAULT_CONFIG["status_page_path"])
    thresholds = config.get("thresholds", {})
    repeat_minutes = float(thresholds.get("alert_repeat_minutes", 60))
    state = load_state(state_path)
    previous_status = state.get("status")
    previous_severity = state.get("severity")
    should_emit = should_emit_alert(state, report, repeat_minutes)
    history = state.get("history", [])
    history.append(history_item(report))
    history = history[-100:]
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
    write_status_page(status_page_path, report, state)

    if should_emit:
        print(format_alert(report, previous_status, previous_severity))
    return 0 if report["status"] == "ok" else 1


def format_alert(
    report: dict[str, Any],
    previous_status: str | None = None,
    previous_severity: str | None = None,
) -> str:
    failed_checks = [check for check in report["checks"] if not check["ok"]]
    if report["severity"] == "ok" and previous_status and previous_status != "ok":
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
    recovery = report.get("recovery") or {}
    if recovery.get("action") != "none":
        lines.append(
            "Recovery: "
            f"{recovery.get('action')} "
            f"(mode={recovery.get('mode')}, restart_command_configured={recovery.get('restart_command_configured')})"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report local Kaspa node health.")
    parser.add_argument("-c", "--config", type=Path, help="Path to config JSON.")
    parser.add_argument("--json", action="store_true", help="Print a JSON health report.")
    parser.add_argument("--alert", action="store_true", help="Print only alert transition output and update state.")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.json:
        report = build_report(config)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "ok" else 1
    if args.alert:
        return alert(config)
    return status(config)


if __name__ == "__main__":
    raise SystemExit(main())
