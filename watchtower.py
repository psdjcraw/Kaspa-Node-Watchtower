#!/usr/bin/env python3
"""Small local health reporter for a Kaspa node."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


VERSION = "0.8.1"

DEFAULT_CONFIG = {
    "config_version": 1,
    "node_name": "kaspa-local",
    "process_match": "kaspad",
    "log_scan_bytes": 100_000_000,
    "bps_highway_log_scan_bytes": 5_000_000,
    "log_path": "",
    "data_dir": "",
    "rpc_endpoint": "",
    "grpc_endpoint": "",
    "state_path": "state/watchtower-state.json",
    "status_page_path": "state/status.html",
    "canvas_status_page_path": "",
    "stream_page_path": "state/stream.html",
    "canvas_stream_page_path": "",
    "bps_highway_snapshot_path": "state/bps-highway.json",
    "benchmark_path": "state/benchmarks.jsonl",
    "sqlite_history_path": "state/watchtower-history.sqlite",
    "market_snapshot_path": "state/market-snapshots.jsonl",
    "prometheus_metrics_path": "state/watchtower.prom",
    "recovery_history_path": "state/recovery-history.jsonl",
    "retention": {
        "state_history_entries": 100,
        "benchmark_entries": 1000,
    },
    "maintenance": {
        "enabled": False,
        "mute_until": "",
        "critical_only": True,
        "reason": "",
    },
    "wallet": {
        "enabled": False,
        "alert_on_change": True,
        "alert_min_delta_sompi": 1,
        "alert_directions": "all",
        "large_outgoing_alert_sompi": 0,
        "mining_reward_stale_hours": 0,
        "event_history_entries": 50,
        "watch_addresses": [],
    },
    "mining": {
        "enabled": False,
        "mode": "macos-gpu-experimental",
        "process_match": "",
        "log_path": "",
        "pool_url": "",
        "wallet_address": "",
        "worker_name": "",
        "expected_hashrate_min_hs": 0,
        "stale_share_minutes": 0,
    },
    "whale_watch": {
        "enabled": False,
        "confirmed_enabled": True,
        "min_amount_sompi": 100_000_000_000_000,
        "alert_enabled": True,
        "event_history_entries": 100,
        "explorer_base_url": "",
        "explorer_tx_path": "/txs/{tx_id}",
        "explorer_address_path": "/addresses/{address}",
    },
    "indexer": {
        "enabled": False,
        "base_url": "http://localhost:8500",
        "health_path": "/api/health",
        "metrics_path": "/api/metrics",
        "timeout_seconds": 2,
        "require_metrics": False,
        "max_lag_seconds": 60,
        "max_checkpoint_age_seconds": 300,
        "transaction_path": "/api/transactions/{tx_id}",
        "address_transactions_path": "/api/addresses/{address}/transactions",
        "address_balance_path": "/api/addresses/{address}/balance",
        "address_utxos_path": "/api/addresses/{address}/utxos",
        "search_path": "/api/search?q={query}",
    },
    "indexer_watch": {
        "enabled": False,
        "alert_enabled": True,
        "event_history_entries": 100,
        "watch_addresses": [],
    },
    "sdk_probe": {
        "enabled": False,
        "endpoint": "",
        "network_id": "mainnet",
        "encoding": "borsh",
        "timeout_seconds": 5,
        "python_bin": "",
        "subscription_enabled": False,
        "subscription_duration_seconds": 5,
        "subscription_watch_addresses": [],
        "event_history_entries": 100,
        "alert_enabled": True,
        "require_ok": False,
    },
    "thresholds": {
        "alert_repeat_minutes": 60,
        "stale_log_minutes": 15,
        "stale_processed_stats_minutes": 3,
        "progress_window_minutes": 10,
        "min_relay_blocks_in_window": 1,
        "min_peer_count": 1,
        "min_active_peer_count": 1,
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
        "policy": {
            "require_critical": True,
            "min_consecutive_failures": 3,
            "min_incident_minutes": 5,
            "require_same_failed_checks": True,
            "allow_during_maintenance": False,
        },
    },
}

NODE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
NODE_NETWORK_HINT_PATTERN = re.compile(r"(mainnet|testnet|tn\d*|simnet|devnet|test)")
MULTI_NODE_THRESHOLD_ENV_SPECS = {
    "MULTI_NODE_DAA_LAG_WARNING": "integer >= 0",
    "MULTI_NODE_BLOCK_LAG_WARNING": "integer >= 0",
    "MULTI_NODE_STALE_MINUTES": "number >= 1",
    "MULTI_NODE_PEER_LAG_WARNING": "integer >= 0",
    "MULTI_NODE_PROCESSED_AGE_LAG_WARNING": "number >= 0",
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
    "active_peer_count",
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


def load_raw_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError("config root must be a JSON object")
    return loaded


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=False)
        handle.write("\n")


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


def sdk_probe_config(config: dict) -> dict[str, Any]:
    return {**DEFAULT_CONFIG["sdk_probe"], **(config.get("sdk_probe") or {})}


def sdk_subscription_watch_targets(config: dict[str, Any]) -> list[dict[str, str]]:
    sdk_config = sdk_probe_config(config)
    targets: list[dict[str, str]] = []
    targets.extend(normalize_watch_addresses(sdk_config.get("subscription_watch_addresses")))
    targets.extend(normalize_watch_addresses((config.get("wallet") or {}).get("watch_addresses")))
    targets.extend(normalize_watch_addresses((config.get("indexer_watch") or {}).get("watch_addresses")))
    mining_address = str((config.get("mining") or {}).get("wallet_address") or "").strip()
    if mining_address:
        targets.append({"label": "mining", "address": mining_address})
    by_address: dict[str, dict[str, str]] = {}
    for target in targets:
        address = str(target.get("address") or "").strip()
        if not looks_like_kaspa_address(address):
            continue
        label = str(target.get("label") or "unlabeled").strip() or "unlabeled"
        by_address.setdefault(address, {"address": address, "label": label})
    return list(by_address.values())


def run_sdk_probe_subprocess(
    python_bin: str,
    endpoint: str,
    network_id: str,
    encoding: str,
    timeout_seconds: float,
    *,
    subscriptions: bool = False,
    duration_seconds: float | None = None,
    watch_addresses: list[str] | None = None,
) -> dict[str, Any]:
    probe_path = Path(__file__).resolve().parent / "kaspa_sdk_probe.py"
    command = [
        python_bin,
        str(probe_path),
        "--endpoint",
        endpoint,
        "--network-id",
        network_id,
        "--timeout",
        str(timeout_seconds),
        "--encoding",
        encoding,
    ]
    command_timeout = timeout_seconds + 2
    if subscriptions:
        command.append("--subscriptions")
        if duration_seconds is not None:
            command.extend(["--duration", str(duration_seconds)])
            command_timeout += duration_seconds
        for address in watch_addresses or []:
            command.extend(["--address", address])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=command_timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"SDK probe subprocess exited {completed.returncode}: {detail}")
    return json.loads(completed.stdout)


def fetch_optional_sdk_metrics(config: dict, fallback_endpoint: str) -> dict[str, Any]:
    sdk_config = sdk_probe_config(config)
    if not sdk_config.get("enabled"):
        return {"enabled": False, "configured": False, "ok": False, "detail": "disabled"}
    endpoint = str(sdk_config.get("endpoint") or fallback_endpoint or "")
    metrics: dict[str, Any] = {
        "enabled": True,
        "configured": bool(endpoint),
        "ok": False,
        "endpoint": endpoint,
        "network_id": sdk_config.get("network_id") or "mainnet",
        "encoding": sdk_config.get("encoding") or "borsh",
    }
    if not endpoint:
        metrics["detail"] = "not configured"
        return metrics
    timeout_seconds = float(sdk_config.get("timeout_seconds") or 5)
    network_id = str(sdk_config.get("network_id") or "mainnet")
    encoding = str(sdk_config.get("encoding") or "borsh")
    python_bin = str(sdk_config.get("python_bin") or "")
    if python_bin:
        try:
            result = run_sdk_probe_subprocess(python_bin, endpoint, network_id, encoding, timeout_seconds)
        except Exception as exc:
            metrics["detail"] = f"SDK probe subprocess failed: {exc}"
            return metrics
    else:
        try:
            from kaspa_sdk_probe import collect_subscription_metrics, fetch_sdk_metrics
        except Exception as exc:
            metrics["detail"] = f"SDK probe unavailable: {exc}"
            return metrics
        result = fetch_sdk_metrics(
            endpoint,
            network_id=network_id,
            timeout=timeout_seconds,
            encoding=encoding,
        )
    if sdk_config.get("subscription_enabled"):
        duration_seconds = float(sdk_config.get("subscription_duration_seconds") or 5)
        watch_targets = sdk_subscription_watch_targets(config)
        watch_addresses = [item["address"] for item in watch_targets]
        try:
            if python_bin:
                subscription_result = run_sdk_probe_subprocess(
                    python_bin,
                    endpoint,
                    network_id,
                    encoding,
                    timeout_seconds,
                    subscriptions=True,
                    duration_seconds=duration_seconds,
                    watch_addresses=watch_addresses,
                )
            else:
                subscription_result = collect_subscription_metrics(
                    endpoint,
                    network_id=network_id,
                    timeout=timeout_seconds,
                    encoding=encoding,
                    duration=duration_seconds,
                    watch_addresses=watch_addresses,
                )
            result.update(subscription_result)
            result["subscription_watch_targets"] = watch_targets
        except Exception as exc:
            result.update(
                {
                    "subscription_enabled": True,
                    "subscription_ok": False,
                    "subscription_detail": f"SDK subscription probe failed: {exc}",
                }
            )
    result.update(
        {
            "enabled": True,
            "configured": True,
            "endpoint": endpoint,
            "network_id": network_id or result.get("network_id") or "mainnet",
            "encoding": encoding or result.get("encoding") or "borsh",
            "python_bin": python_bin or sys.executable,
        }
    )
    if not result.get("ok"):
        result["detail"] = result.get("detail") or result.get("error") or "SDK probe failed"
    return result


def sdk_subscription_event_key(event: dict[str, Any]) -> str:
    return "|".join(
        [
            str(event.get("source") or "sdk_subscription"),
            str(event.get("direction") or "unknown"),
            str(event.get("tx_id") or ""),
            str(event.get("address") or ""),
            str(int(event.get("amount_sompi") or 0)),
        ]
    )


def update_sdk_subscription_event_state(
    state: dict[str, Any],
    report: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    sdk_config = sdk_probe_config(config)
    sdk_metrics = report.get("sdk_metrics") or {}
    raw_events = [
        item
        for item in (sdk_metrics.get("subscription_utxo_events") or [])
        if isinstance(item, dict)
    ]
    limit = positive_int(sdk_config.get("event_history_entries"), DEFAULT_CONFIG["sdk_probe"]["event_history_entries"])
    events = list(state.get("sdk_subscription_events") or [])
    seen = {str(event.get("event_key") or sdk_subscription_event_key(event)) for event in events}
    new_events = []
    for raw in raw_events:
        event = dict(raw)
        event.setdefault("observed_at", report.get("checked_at"))
        event.setdefault("source", "sdk_subscription")
        event.setdefault("type", "utxo_changed")
        event["event_key"] = str(event.get("event_key") or sdk_subscription_event_key(event))
        if event["event_key"] in seen:
            continue
        seen.add(event["event_key"])
        new_events.append(event)
    events.extend(new_events)
    state["sdk_subscription_events"] = events[-limit:]
    sdk_metrics["events"] = list(state.get("sdk_subscription_events") or [])
    sdk_metrics["new_events"] = new_events
    sdk_metrics["event_history_entries"] = len(sdk_metrics["events"])
    report["sdk_metrics"] = sdk_metrics
    return new_events


def kas_from_sompi(value: Any) -> float | None:
    parsed = numeric(value)
    if parsed is None:
        return None
    return parsed / 100_000_000


def format_kas(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed / 100_000_000:.8f} KAS"


def mining_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("mining") if isinstance(config.get("mining"), dict) else {}
    return {**DEFAULT_CONFIG["mining"], **raw}


def looks_like_kaspa_address(value: Any) -> bool:
    address = str(value or "").strip()
    return bool(re.fullmatch(r"kaspa[a-z]*:[a-z0-9]{20,}", address))


def mining_wallet_address(config: dict[str, Any]) -> tuple[str, str]:
    mining = mining_config(config)
    address = str(mining.get("wallet_address") or "").strip()
    if address:
        return address, "mining.wallet_address"
    for entry in wallet_watch_entries(config):
        if "mining" in str(entry.get("label") or "").lower():
            fallback = str(entry.get("address") or "").strip()
            if fallback:
                return fallback, "wallet.watch_addresses"
    return "", "none"


def hashrate_to_hs(value: float, unit: str) -> float:
    multipliers = {
        "h": 1,
        "kh": 1_000,
        "mh": 1_000_000,
        "gh": 1_000_000_000,
        "th": 1_000_000_000_000,
    }
    return value * multipliers.get(unit.lower(), 1)


def format_hashrate_local(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    units = [("TH/s", 1_000_000_000_000), ("GH/s", 1_000_000_000), ("MH/s", 1_000_000), ("KH/s", 1_000)]
    for suffix, divisor in units:
        if abs(parsed) >= divisor:
            return f"{parsed / divisor:.2f} {suffix}"
    return f"{parsed:.2f} H/s"


def parse_miner_log(lines: Iterable[str]) -> dict[str, Any]:
    hashrate_pattern = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?H)\s*/?\s*s", re.IGNORECASE)
    accepted_pattern = re.compile(r"\baccepted\b|\bshare accepted\b", re.IGNORECASE)
    rejected_pattern = re.compile(r"\brejected\b|\bshare rejected\b", re.IGNORECASE)
    result: dict[str, Any] = {
        "hashrate_hs": None,
        "accepted_shares": 0,
        "rejected_shares": 0,
        "last_share_at": "",
        "last_hashrate_at": "",
        "latest_line": "",
    }
    for line in lines:
        timestamp = parse_log_timestamp(line)
        match = hashrate_pattern.search(line)
        if match:
            result["hashrate_hs"] = hashrate_to_hs(float(match.group(1)), match.group(2))
            result["last_hashrate_at"] = timestamp.isoformat() if timestamp else ""
        if accepted_pattern.search(line):
            result["accepted_shares"] = int(result.get("accepted_shares") or 0) + 1
            result["last_share_at"] = timestamp.isoformat() if timestamp else ""
        if rejected_pattern.search(line):
            result["rejected_shares"] = int(result.get("rejected_shares") or 0) + 1
            result["last_share_at"] = timestamp.isoformat() if timestamp else result.get("last_share_at", "")
        if line.strip():
            result["latest_line"] = line.strip()[-240:]
    return result


def fetch_optional_mining_status(config: dict[str, Any]) -> dict[str, Any]:
    mining = mining_config(config)
    process_match = str(mining.get("process_match") or "").strip()
    processes = find_processes(process_match) if process_match else []
    address, address_source = mining_wallet_address(config)
    status: dict[str, Any] = {
        "enabled": bool(mining.get("enabled", False)),
        "mode": mining.get("mode") or "disabled",
        "configured": bool(process_match or mining.get("log_path")),
        "ok": False,
        "running": bool(processes),
        "process_match": process_match,
        "processes": processes,
        "pool_url": mining.get("pool_url") or "",
        "worker_name": mining.get("worker_name") or "",
        "wallet_address": address,
        "wallet_address_source": address_source,
        "hashrate_hs": None,
        "accepted_shares": 0,
        "rejected_shares": 0,
        "last_share_at": "",
        "last_share_age_seconds": None,
        "detail": "disabled",
    }
    if not status["enabled"]:
        return status
    address_ok = looks_like_kaspa_address(address)
    if not status["configured"]:
        status["detail"] = "no miner process_match or log_path configured"
        return status

    log_path = Path(str(mining.get("log_path") or ""))
    if log_path.exists():
        parsed = parse_miner_log(tail_lines(log_path, int(config.get("log_scan_bytes") or DEFAULT_CONFIG["log_scan_bytes"])))
        status.update(parsed)
    elif mining.get("log_path"):
        status["log_error"] = f"missing ({log_path})"

    checked_at = dt.datetime.now().astimezone()
    latest_share = parse_iso_datetime(str(status.get("last_share_at") or ""))
    if latest_share is not None:
        status["last_share_age_seconds"] = max(0.0, (checked_at - latest_share).total_seconds())
    expected_hashrate = float(mining.get("expected_hashrate_min_hs", 0) or 0)
    stale_share_minutes = float(mining.get("stale_share_minutes", 0) or 0)
    hashrate_ok = expected_hashrate <= 0 or float(status.get("hashrate_hs") or 0) >= expected_hashrate
    share_ok = stale_share_minutes <= 0 or (
        status.get("last_share_age_seconds") is not None
        and float(status["last_share_age_seconds"]) <= stale_share_minutes * 60
    )
    status["ok"] = bool(status["running"] and hashrate_ok and share_ok and address_ok)
    detail_parts = [
        "running" if status["running"] else "not running",
        "address=set" if address_ok else "address=missing",
        f"hashrate={format_hashrate_local(status.get('hashrate_hs'))}",
        f"accepted={status.get('accepted_shares', 0)}",
        f"rejected={status.get('rejected_shares', 0)}",
    ]
    if status.get("log_error"):
        detail_parts.append(str(status["log_error"]))
    if expected_hashrate > 0:
        detail_parts.append(f"min_hashrate={format_hashrate_local(expected_hashrate)}")
    if stale_share_minutes > 0:
        age = status.get("last_share_age_seconds")
        age_text = "unknown" if age is None else f"{float(age) / 60:.1f}m"
        detail_parts.append(f"share_age={age_text} threshold={stale_share_minutes:g}m")
    status["detail"] = "; ".join(detail_parts)
    return status


def apply_mining_policy_checks(report: dict[str, Any], config: dict[str, Any]) -> None:
    mining = report.get("mining") or {}
    if not mining.get("enabled"):
        return
    address = str(mining.get("wallet_address") or "")
    report["checks"].append(
        Check(
            "mining_wallet_address",
            looks_like_kaspa_address(address),
            f"source={mining.get('wallet_address_source', 'unknown')} address={'set' if address else 'missing'}",
        ).as_dict()
    )
    report["checks"].append(Check("mining_process", bool(mining.get("running")), mining.get("detail", "unknown")).as_dict())
    expected_hashrate = float(mining_config(config).get("expected_hashrate_min_hs", 0) or 0)
    if expected_hashrate > 0:
        hashrate = float(mining.get("hashrate_hs") or 0)
        report["checks"].append(
            Check(
                "mining_hashrate",
                hashrate >= expected_hashrate,
                f"hashrate={format_hashrate_local(hashrate)} threshold={format_hashrate_local(expected_hashrate)}",
            ).as_dict()
        )
    stale_share_minutes = float(mining_config(config).get("stale_share_minutes", 0) or 0)
    if stale_share_minutes > 0:
        age = mining.get("last_share_age_seconds")
        ok = age is not None and float(age) <= stale_share_minutes * 60
        age_text = "unknown" if age is None else f"{float(age) / 60:.1f}m"
        report["checks"].append(
            Check(
                "mining_share_freshness",
                ok,
                f"last share age={age_text} threshold={stale_share_minutes:g}m",
            ).as_dict()
        )
    recalculate_report_health(report, config)


def whale_watch_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("whale_watch") if isinstance(config.get("whale_watch"), dict) else {}
    return {**DEFAULT_CONFIG["whale_watch"], **raw}


def whale_explorer_url(config: dict[str, Any], kind: str, value: Any) -> str:
    whale_config = whale_watch_config(config)
    base = str(whale_config.get("explorer_base_url") or "").strip().rstrip("/")
    raw_value = str(value or "").strip()
    if not base or not raw_value:
        return ""
    if not re.fullmatch(r"https?://[^/\s]+.*", base):
        return ""
    if kind == "tx":
        template = str(whale_config.get("explorer_tx_path") or DEFAULT_CONFIG["whale_watch"]["explorer_tx_path"])
        replacements = {"tx_id": urllib.parse.quote(raw_value, safe=""), "address": ""}
    elif kind == "address":
        template = str(whale_config.get("explorer_address_path") or DEFAULT_CONFIG["whale_watch"]["explorer_address_path"])
        replacements = {"tx_id": "", "address": urllib.parse.quote(raw_value, safe=":")}
    else:
        return ""
    try:
        path = template.format(**replacements)
    except (KeyError, ValueError):
        return ""
    if re.fullmatch(r"https?://[^\s]+", path):
        return path
    return f"{base}/{path.lstrip('/')}"


def enrich_whale_event_links(event: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    tx_url = whale_explorer_url(config, "tx", event.get("tx_id"))
    address_url = whale_explorer_url(config, "address", event.get("address"))
    if tx_url:
        event["tx_url"] = tx_url
    if address_url:
        event["address_url"] = address_url
    return event


def whale_event_key(source: Any, tx_id: Any, amount_sompi: Any, address: Any = "") -> str:
    return f"{source or ''}|{tx_id or ''}|{int(amount_sompi or 0)}|{address or ''}"


def whale_events_from_mempool(mempool: dict[str, Any], config: dict[str, Any], checked_at: str) -> list[dict[str, Any]]:
    whale_config = whale_watch_config(config)
    threshold = int(whale_config.get("min_amount_sompi") or DEFAULT_CONFIG["whale_watch"]["min_amount_sompi"])
    events = []
    for entry in mempool.get("entries") or []:
        tx_id = str(entry.get("tx_id") or "")
        outputs = entry.get("outputs") or []
        for output in outputs:
            amount = int(output.get("amount_sompi") or 0)
            if amount < threshold:
                continue
            address = str(output.get("address") or "")
            events.append(
                enrich_whale_event_links(
                    {
                        "event_key": whale_event_key("mempool", tx_id, amount, address),
                        "observed_at": checked_at,
                        "type": "whale_tx_pending",
                        "source": "mempool",
                        "tx_id": tx_id,
                        "address": address,
                        "amount_sompi": amount,
                        "amount_kas": kas_from_sompi(amount),
                        "threshold_sompi": threshold,
                        "threshold_kas": kas_from_sompi(threshold),
                        "fee_sompi": entry.get("fee_sompi"),
                        "total_output_sompi": entry.get("total_output_sompi"),
                        "largest_output_sompi": entry.get("largest_output_sompi"),
                        "input_count": entry.get("input_count"),
                        "output_count": entry.get("output_count"),
                    },
                    config,
                )
            )
    events.sort(key=lambda item: int(item.get("amount_sompi") or 0), reverse=True)
    return events


def whale_events_from_confirmed(chain: dict[str, Any], config: dict[str, Any], checked_at: str) -> list[dict[str, Any]]:
    whale_config = whale_watch_config(config)
    threshold = int(whale_config.get("min_amount_sompi") or DEFAULT_CONFIG["whale_watch"]["min_amount_sompi"])
    events = []
    for entry in chain.get("entries") or []:
        tx_id = str(entry.get("tx_id") or "")
        for output in entry.get("outputs") or []:
            amount = int(output.get("amount_sompi") or 0)
            if amount < threshold:
                continue
            address = str(output.get("address") or "")
            events.append(
                enrich_whale_event_links(
                    {
                        "event_key": whale_event_key("confirmed", tx_id, amount, address),
                        "observed_at": checked_at,
                        "type": "whale_tx_confirmed",
                        "source": "confirmed",
                        "tx_id": tx_id,
                        "address": address,
                        "amount_sompi": amount,
                        "amount_kas": kas_from_sompi(amount),
                        "threshold_sompi": threshold,
                        "threshold_kas": kas_from_sompi(threshold),
                        "accepting_block_hash": entry.get("accepting_block_hash") or "",
                        "total_output_sompi": entry.get("total_output_sompi"),
                        "largest_output_sompi": entry.get("largest_output_sompi"),
                        "input_count": entry.get("input_count"),
                        "output_count": entry.get("output_count"),
                    },
                    config,
                )
            )
    events.sort(key=lambda item: int(item.get("amount_sompi") or 0), reverse=True)
    return events


def fetch_optional_whale_watch(config: dict[str, Any], endpoint: str) -> dict[str, Any]:
    whale_config = whale_watch_config(config)
    whale: dict[str, Any] = {
        "enabled": bool(whale_config.get("enabled", False)),
        "confirmed_enabled": bool(whale_config.get("confirmed_enabled", True)),
        "ok": False,
        "configured": bool(endpoint),
        "min_amount_sompi": int(whale_config.get("min_amount_sompi") or DEFAULT_CONFIG["whale_watch"]["min_amount_sompi"]),
        "min_amount_kas": kas_from_sompi(whale_config.get("min_amount_sompi") or DEFAULT_CONFIG["whale_watch"]["min_amount_sompi"]),
        "alert_enabled": bool(whale_config.get("alert_enabled", True)),
        "event_history_entries": positive_int(whale_config.get("event_history_entries"), DEFAULT_CONFIG["whale_watch"]["event_history_entries"]),
        "explorer_base_url": str(whale_config.get("explorer_base_url") or ""),
        "explorer_tx_path": str(whale_config.get("explorer_tx_path") or DEFAULT_CONFIG["whale_watch"]["explorer_tx_path"]),
        "explorer_address_path": str(whale_config.get("explorer_address_path") or DEFAULT_CONFIG["whale_watch"]["explorer_address_path"]),
        "mempool_entries": 0,
        "candidates": [],
        "detail": "disabled",
    }
    if not whale["enabled"]:
        return whale
    if not endpoint:
        whale["detail"] = "no gRPC endpoint configured"
        return whale
    try:
        from kaspa_grpc_probe import fetch_mempool_entries
    except Exception as exc:
        whale["detail"] = f"whale gRPC probe unavailable: {exc}"
        return whale
    mempool = fetch_mempool_entries(endpoint)
    whale["mempool"] = mempool
    whale["mempool_entries"] = len(mempool.get("entries") or [])
    whale["ok"] = bool(mempool.get("ok"))
    whale["detail"] = "mempool read ok" if mempool.get("ok") else mempool.get("detail") or mempool.get("error") or "mempool read failed"
    checked_at = dt.datetime.now().astimezone().isoformat()
    whale["candidates"] = whale_events_from_mempool(mempool, config, checked_at)
    return whale


def whale_current_chain_hash(report: dict[str, Any]) -> str:
    grpc_metrics = report.get("grpc_metrics") or {}
    return str(grpc_metrics.get("sink") or grpc_metrics.get("virtual_parent_hash") or "")


def update_whale_confirmed_candidates(state: dict[str, Any], report: dict[str, Any], config: dict[str, Any]) -> None:
    whale = report.get("whale_watch") or {}
    whale_config = whale_watch_config(config)
    current_hash = whale_current_chain_hash(report)
    whale["confirmed_start_hash"] = state.get("whale_watch_chain_hash") or ""
    whale["confirmed_current_hash"] = current_hash
    if not whale.get("enabled") or not bool(whale_config.get("confirmed_enabled", True)):
        if current_hash:
            state["whale_watch_chain_hash"] = current_hash
        return
    start_hash = str(state.get("whale_watch_chain_hash") or "")
    if not start_hash:
        whale["confirmed_detail"] = "baseline chain hash recorded"
        if current_hash:
            state["whale_watch_chain_hash"] = current_hash
        return
    if not current_hash:
        whale["confirmed_detail"] = "no current chain hash available"
        return
    if start_hash == current_hash:
        whale["confirmed_detail"] = "no virtual chain movement"
        return
    try:
        from kaspa_grpc_probe import fetch_virtual_chain_transactions
    except Exception as exc:
        whale["confirmed_detail"] = f"confirmed gRPC probe unavailable: {exc}"
        return
    endpoint = report.get("grpc_endpoint") or report.get("rpc_endpoint") or ""
    chain = fetch_virtual_chain_transactions(endpoint, start_hash)
    whale["confirmed"] = chain
    whale["confirmed_detail"] = "confirmed scan ok" if chain.get("ok") else chain.get("detail") or chain.get("error") or "confirmed scan failed"
    checked_at = report.get("checked_at") or dt.datetime.now().astimezone().isoformat()
    confirmed_candidates = whale_events_from_confirmed(chain, config, checked_at)
    whale["confirmed_candidates"] = confirmed_candidates
    whale["candidates"] = list(whale.get("candidates") or []) + confirmed_candidates
    if current_hash:
        state["whale_watch_chain_hash"] = current_hash


def update_whale_event_state(state: dict[str, Any], report: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    whale = report.get("whale_watch") or {}
    candidates = list(whale.get("candidates") or [])
    if not candidates:
        whale["events"] = [enrich_whale_event_links(dict(event), config) for event in list(state.get("whale_events") or [])]
        return []
    whale_config = whale_watch_config(config)
    limit = positive_int(whale_config.get("event_history_entries"), DEFAULT_CONFIG["whale_watch"]["event_history_entries"])
    events = [enrich_whale_event_links(dict(event), config) for event in list(state.get("whale_events") or [])]
    seen = {str(event.get("event_key") or whale_event_key(event.get("source"), event.get("tx_id"), event.get("amount_sompi"), event.get("address"))) for event in events}
    new_events = []
    for event in candidates:
        enrich_whale_event_links(event, config)
        key = str(event.get("event_key") or whale_event_key(event.get("source"), event.get("tx_id"), event.get("amount_sompi"), event.get("address")))
        if key in seen:
            continue
        event["event_key"] = key
        if event.get("source") == "confirmed":
            for existing in events:
                if existing.get("tx_id") == event.get("tx_id") and existing.get("source") == "mempool":
                    existing["status"] = "confirmed"
                    existing["confirmed_at"] = event.get("observed_at")
                    existing["accepting_block_hash"] = event.get("accepting_block_hash")
        seen.add(key)
        new_events.append(event)
    events.extend(new_events)
    state["whale_events"] = events[-limit:]
    whale["events"] = list(state.get("whale_events") or [])
    whale["new_events"] = new_events
    return new_events


def whale_watch_summary(events: list[dict[str, Any]], *, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now().astimezone()
    cutoff = now - dt.timedelta(hours=24)
    recent = []
    for event in events:
        observed = parse_iso_datetime(str(event.get("observed_at") or ""))
        if observed is not None and observed >= cutoff:
            recent.append(event)
    latest = max(
        (event for event in events if parse_iso_datetime(str(event.get("observed_at") or "")) is not None),
        key=lambda item: parse_iso_datetime(str(item.get("observed_at") or "")),
        default={},
    )
    return {
        "total_events": len(events),
        "count_24h": len(recent),
        "pending_24h": sum(1 for event in recent if event.get("source") == "mempool"),
        "confirmed_24h": sum(1 for event in recent if event.get("source") == "confirmed"),
        "volume_24h_sompi": sum(int(event.get("amount_sompi") or 0) for event in recent),
        "volume_24h_kas": kas_from_sompi(sum(int(event.get("amount_sompi") or 0) for event in recent)) or 0.0,
        "latest_amount_sompi": latest.get("amount_sompi"),
        "latest_amount_kas": kas_from_sompi(latest.get("amount_sompi")),
        "latest_tx_id": latest.get("tx_id") or "",
        "latest_observed_at": latest.get("observed_at") or "",
    }


def wallet_watch_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    wallet_config = config.get("wallet") or {}
    raw_entries = wallet_config.get("watch_addresses") or []
    entries: list[dict[str, Any]] = []
    for item in raw_entries:
        if isinstance(item, str):
            address = item.strip()
            label = ""
        elif isinstance(item, dict):
            address = str(item.get("address") or "").strip()
            label = str(item.get("label") or "").strip()
        else:
            continue
        if address:
            entry = {"address": address, "label": label}
            if isinstance(item, dict):
                for key in ("alert_enabled", "alert_min_delta_sompi", "alert_directions"):
                    if key in item:
                        entry[key] = item[key]
            entries.append(entry)
    return entries


def wallet_policy_by_address(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["address"]: entry for entry in wallet_watch_entries(config)}


def wallet_direction_for_delta(delta_sompi: int) -> str:
    if delta_sompi > 0:
        return "incoming"
    if delta_sompi < 0:
        return "outgoing"
    return "unchanged"


def wallet_direction_allowed(direction: str, allowed: Any) -> bool:
    if allowed in (None, "", "all"):
        return True
    if isinstance(allowed, str):
        return direction == allowed
    if isinstance(allowed, list):
        return direction in {str(item) for item in allowed}
    return True


def fetch_optional_wallet_balances(config: dict[str, Any], endpoint: str) -> dict[str, Any]:
    wallet_config = config.get("wallet") or {}
    entries = wallet_watch_entries(config)
    wallet: dict[str, Any] = {
        "enabled": bool(wallet_config.get("enabled", False)),
        "configured": bool(entries),
        "ok": False,
        "entries": [],
        "total_sompi": 0,
        "total_kas": 0.0,
        "detail": "disabled",
    }
    if not wallet["enabled"]:
        return wallet
    if not entries:
        wallet["detail"] = "no watch addresses configured"
        return wallet
    if not endpoint:
        wallet["detail"] = "no gRPC endpoint configured"
        return wallet
    try:
        from kaspa_grpc_probe import fetch_balances_by_addresses, fetch_mempool_entries_by_addresses
    except Exception as exc:
        wallet["detail"] = f"wallet gRPC probe unavailable: {exc}"
        return wallet

    addresses = [entry["address"] for entry in entries]
    labels = {entry["address"]: entry["label"] for entry in entries}
    result = fetch_balances_by_addresses(endpoint, addresses)
    result_entries = {entry.get("address"): entry for entry in result.get("entries", [])}
    wallet_entries = []
    entry_errors = []
    for address in addresses:
        balance_entry = result_entries.get(address) or {"address": address, "balance_sompi": 0}
        error = balance_entry.get("error")
        if error:
            entry_errors.append(f"{address}: {error}")
        balance_sompi = int(balance_entry.get("balance_sompi") or 0)
        wallet_entries.append(
            {
                "address": address,
                "label": labels.get(address) or "",
                "balance_sompi": balance_sompi,
                "balance_kas": kas_from_sompi(balance_sompi),
                "error": error,
            }
        )

    total_sompi = sum(entry["balance_sompi"] for entry in wallet_entries)
    ok = bool(result.get("ok")) and not entry_errors
    detail = "read ok" if ok else result.get("detail") or result.get("error") or "; ".join(entry_errors) or "failed"
    wallet.update(
        {
            "ok": ok,
            "endpoint": endpoint,
            "entries": wallet_entries,
            "pending": fetch_mempool_entries_by_addresses(endpoint, addresses),
            "total_sompi": total_sompi,
            "total_kas": kas_from_sompi(total_sompi),
            "detail": detail,
        }
    )
    return wallet


def wallet_entry_map(wallet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(entry.get("address")): entry
        for entry in wallet.get("entries") or []
        if entry.get("address")
    }


def wallet_change_summary(
    current_wallet: dict[str, Any],
    previous_wallet: dict[str, Any] | None,
    *,
    min_delta_sompi: int = 1,
) -> dict[str, Any]:
    previous_wallet = previous_wallet or {}
    current_entries = wallet_entry_map(current_wallet)
    previous_entries = wallet_entry_map(previous_wallet)
    addresses = sorted(set(current_entries) | set(previous_entries))
    entry_changes = []
    for address in addresses:
        current_entry = current_entries.get(address) or {}
        previous_entry = previous_entries.get(address) or {}
        current_balance = int(current_entry.get("balance_sompi") or 0)
        previous_balance = int(previous_entry.get("balance_sompi") or 0)
        delta = current_balance - previous_balance
        if abs(delta) < min_delta_sompi:
            continue
        entry_changes.append(
            {
                "address": address,
                "label": current_entry.get("label") or previous_entry.get("label") or "",
                "previous_sompi": previous_balance,
                "current_sompi": current_balance,
                "delta_sompi": delta,
                "delta_kas": kas_from_sompi(delta),
            }
        )

    current_total = int(current_wallet.get("total_sompi") or 0)
    previous_total = int(previous_wallet.get("total_sompi") or 0)
    total_delta = current_total - previous_total
    changed = bool(entry_changes) or abs(total_delta) >= min_delta_sompi
    return {
        "changed": changed,
        "previous_total_sompi": previous_total,
        "current_total_sompi": current_total,
        "total_delta_sompi": total_delta,
        "total_delta_kas": kas_from_sompi(total_delta),
        "entries": entry_changes,
    }


def apply_wallet_change_detection(report: dict[str, Any], state: dict[str, Any], config: dict) -> str | None:
    wallet = report.get("wallet") or {}
    wallet_change = wallet.get("change") or {}
    wallet_config = config.get("wallet") or {}
    if not wallet.get("enabled") or not wallet.get("ok"):
        wallet["change"] = {"changed": False, "detail": wallet.get("detail", "wallet unavailable")}
        return None
    previous_wallet = (state.get("last_report") or {}).get("wallet") or {}
    if not previous_wallet.get("enabled") or not previous_wallet.get("ok"):
        wallet["change"] = {
            "changed": False,
            "previous_total_sompi": wallet.get("total_sompi"),
            "current_total_sompi": wallet.get("total_sompi"),
            "total_delta_sompi": 0,
            "total_delta_kas": 0.0,
            "entries": [],
            "detail": "baseline recorded",
        }
        return None
    min_delta = int(wallet_config.get("alert_min_delta_sompi", 1) or 1)
    change = wallet_change_summary(wallet, previous_wallet, min_delta_sompi=max(1, min_delta))
    address_policies = wallet_policy_by_address(config)
    alert_entries = []
    large_outgoing_entries = []
    large_outgoing_min = int(wallet_config.get("large_outgoing_alert_sompi", 0) or 0)
    for item in change.get("entries") or []:
        delta = int(item.get("delta_sompi") or 0)
        direction = wallet_direction_for_delta(delta)
        policy = address_policies.get(str(item.get("address") or ""), {})
        if policy.get("alert_enabled", True) is False:
            continue
        item_min_delta = int(policy.get("alert_min_delta_sompi", min_delta) or min_delta)
        if abs(delta) < max(1, item_min_delta):
            continue
        if not wallet_direction_allowed(direction, policy.get("alert_directions", wallet_config.get("alert_directions", "all"))):
            continue
        alert_entries.append({**item, "direction": direction})
        if direction == "outgoing" and large_outgoing_min > 0 and abs(delta) >= large_outgoing_min:
            large_outgoing_entries.append({**item, "direction": direction})
    change["alert_entries"] = alert_entries
    change["large_outgoing_entries"] = large_outgoing_entries
    wallet["change"] = change
    if not bool(wallet_config.get("alert_on_change", True)):
        return None
    if large_outgoing_entries:
        return "wallet_large_outgoing"
    if alert_entries:
        return "wallet_changed"
    return None


def wallet_events_from_change(report: dict[str, Any]) -> list[dict[str, Any]]:
    wallet = report.get("wallet") or {}
    change = wallet.get("change") or {}
    if not change.get("changed"):
        return []
    checked_at = report.get("checked_at")
    events = []
    for item in change.get("entries") or []:
        delta = int(item.get("delta_sompi") or 0)
        direction = "incoming" if delta > 0 else "outgoing" if delta < 0 else "unchanged"
        events.append(
            {
                "event_key": wallet_event_key(checked_at, item.get("address"), delta),
                "observed_at": checked_at,
                "type": "balance_change",
                "direction": direction,
                "address": item.get("address"),
                "label": item.get("label") or "",
                "delta_sompi": delta,
                "delta_kas": kas_from_sompi(delta),
                "previous_sompi": item.get("previous_sompi"),
                "current_sompi": item.get("current_sompi"),
            }
        )
    if not events and change.get("total_delta_sompi"):
        delta = int(change.get("total_delta_sompi") or 0)
        events.append(
            {
                "event_key": wallet_event_key(checked_at, "total", delta),
                "observed_at": checked_at,
                "type": "balance_change",
                "direction": "incoming" if delta > 0 else "outgoing",
                "address": "",
                "label": "total",
                "delta_sompi": delta,
                "delta_kas": kas_from_sompi(delta),
                "previous_sompi": change.get("previous_total_sompi"),
                "current_sompi": change.get("current_total_sompi"),
            }
        )
    return events


def wallet_event_key(observed_at: Any, address: Any, delta_sompi: Any) -> str:
    return f"{observed_at}|{address or ''}|{int(delta_sompi or 0)}"


def update_wallet_event_state(state: dict[str, Any], report: dict[str, Any], config: dict) -> list[dict[str, Any]]:
    new_events = wallet_events_from_change(report)
    if not new_events:
        return []
    wallet_config = config.get("wallet") or {}
    limit = positive_int(wallet_config.get("event_history_entries"), 50)
    events = list(state.get("wallet_events") or [])
    seen = {str(event.get("event_key") or wallet_event_key(event.get("observed_at"), event.get("address"), event.get("delta_sompi"))) for event in events}
    deduped = []
    for event in new_events:
        key = str(event.get("event_key") or wallet_event_key(event.get("observed_at"), event.get("address"), event.get("delta_sompi")))
        if key in seen:
            continue
        event["event_key"] = key
        seen.add(key)
        deduped.append(event)
    events.extend(deduped)
    state["wallet_events"] = events[-limit:]
    return deduped


def format_usd_amount(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"${parsed:,.2f}"


def mining_reward_summary(
    events: list[dict[str, Any]],
    *,
    price_usdt: Any = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or dt.datetime.now().astimezone()
    today = now.date()
    price = numeric(price_usdt)
    rewards = []
    for event in events:
        if event.get("direction") != "incoming":
            continue
        label = str(event.get("label") or "").lower()
        if "mining" not in label:
            continue
        delta = int(event.get("delta_sompi") or 0)
        if delta <= 0:
            continue
        observed = parse_iso_datetime(str(event.get("observed_at") or ""))
        rewards.append({**event, "observed": observed, "delta_sompi": delta})

    def in_window(days: int | None) -> list[dict[str, Any]]:
        if days is None:
            return [event for event in rewards if event.get("observed") is not None and event["observed"].date() == today]
        cutoff = now - dt.timedelta(days=days)
        return [event for event in rewards if event.get("observed") is not None and event["observed"] >= cutoff]

    today_events = in_window(None)
    seven_day_events = in_window(7)
    thirty_day_events = in_window(30)

    def total_sompi(items: list[dict[str, Any]]) -> int:
        return sum(int(item.get("delta_sompi") or 0) for item in items)

    today_sompi = total_sompi(today_events)
    seven_day_sompi = total_sompi(seven_day_events)
    thirty_day_sompi = total_sompi(thirty_day_events)
    latest = max((event for event in rewards if event.get("observed") is not None), key=lambda item: item["observed"], default={})
    latest_age_hours = None
    if latest.get("observed") is not None:
        latest_age_hours = max(0.0, (now - latest["observed"]).total_seconds() / 3600)
    today_kas = kas_from_sompi(today_sompi) or 0.0
    seven_day_kas = kas_from_sompi(seven_day_sompi) or 0.0
    thirty_day_kas = kas_from_sompi(thirty_day_sompi) or 0.0
    return {
        "candidate_events": len(rewards),
        "today_sompi": today_sompi,
        "today_kas": today_kas,
        "seven_day_sompi": seven_day_sompi,
        "seven_day_kas": seven_day_kas,
        "thirty_day_sompi": thirty_day_sompi,
        "thirty_day_kas": thirty_day_kas,
        "average_daily_7d_kas": seven_day_kas / 7,
        "average_daily_30d_kas": thirty_day_kas / 30,
        "today_usd": None if price is None else today_kas * price,
        "seven_day_usd": None if price is None else seven_day_kas * price,
        "thirty_day_usd": None if price is None else thirty_day_kas * price,
        "projected_monthly_kas": (seven_day_kas / 7) * 30,
        "projected_monthly_usd": None if price is None else (seven_day_kas / 7) * 30 * price,
        "latest_reward_at": latest.get("observed_at") or "",
        "latest_reward_age_hours": latest_age_hours,
        "price_usdt": price,
    }


def apply_wallet_policy_checks(report: dict[str, Any], config: dict) -> None:
    wallet_config = config.get("wallet") or {}
    stale_hours = float(wallet_config.get("mining_reward_stale_hours", 0) or 0)
    if stale_hours <= 0:
        return
    wallet = report.get("wallet") or {}
    if not wallet.get("enabled"):
        return
    checked_at = parse_iso_datetime(report.get("checked_at")) or dt.datetime.now().astimezone()
    mining = mining_reward_summary(list(wallet.get("events") or []), now=checked_at)
    latest_at = parse_iso_datetime(str(mining.get("latest_reward_at") or ""))
    if latest_at is None:
        ok = False
        detail = f"no mining reward events observed; threshold={stale_hours:g}h"
    else:
        age_hours = max(0.0, (checked_at - latest_at).total_seconds() / 3600)
        ok = age_hours <= stale_hours
        detail = f"latest mining reward age={age_hours:.2f}h threshold={stale_hours:g}h"
    report["checks"].append(Check("mining_reward_freshness", ok, detail).as_dict())
    recalculate_report_health(report, config)


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
    sdk_metrics = fetch_optional_sdk_metrics(config, grpc_endpoint)
    wallet = fetch_optional_wallet_balances(config, grpc_endpoint)
    mining = fetch_optional_mining_status(config)
    whale_watch = fetch_optional_whale_watch(config, grpc_endpoint)
    indexer = fetch_optional_indexer_status(config)

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
                    f"(threshold={min_peer_count}, active={grpc_metrics.get('active_peers')})"
                ),
            )
        )
        min_active_peer_count = int(thresholds.get("min_active_peer_count", 1))
        checks.append(
            Check(
                "active_peer_count",
                int(grpc_metrics.get("active_peers") or 0) >= min_active_peer_count,
                (
                    f"{int(grpc_metrics.get('active_peers') or 0)} active peers "
                    f"(threshold={min_active_peer_count}, total={grpc_metrics.get('peer_count')})"
                ),
            )
        )

    sdk_config = sdk_probe_config(config)
    if sdk_metrics.get("enabled") and (sdk_config.get("require_ok") or sdk_metrics.get("configured")):
        checks.append(
            Check(
                "sdk_probe",
                bool(sdk_metrics.get("ok")) or not bool(sdk_config.get("require_ok")),
                "read ok" if sdk_metrics.get("ok") else sdk_metrics.get("detail", "failed"),
            )
        )

    if indexer.get("enabled"):
        checks.append(
            Check(
                "indexer_health",
                bool(indexer.get("health_ok") or indexer.get("syncing")),
                indexer.get("detail") or "unknown",
            )
        )
        if indexer.get("metrics_ok") or indexer_config(config).get("require_metrics"):
            checks.append(
                Check(
                    "indexer_metrics",
                    bool(indexer.get("metrics_ok")),
                    indexer.get("metrics_detail") or indexer.get("detail") or "unknown",
                )
            )
        if indexer.get("metrics_ok"):
            lag = numeric((indexer.get("metrics") or {}).get("lag_seconds"))
            checkpoint_age = numeric((indexer.get("metrics") or {}).get("checkpoint_age_seconds"))
            checks.append(
                Check(
                    "indexer_lag",
                    bool(indexer.get("lag_ok", True)),
                    "lag=unknown" if lag is None else f"lag={lag:.1f}s",
                )
            )
            checks.append(
                Check(
                    "indexer_checkpoint_freshness",
                    bool(indexer.get("checkpoint_fresh", True)),
                    "checkpoint_age=unknown"
                    if checkpoint_age is None
                    else f"checkpoint_age={checkpoint_age:.1f}s",
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
        "sdk_metrics": sdk_metrics,
        "wallet": wallet,
        "mining": mining,
        "whale_watch": whale_watch,
        "indexer": indexer,
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
    apply_mining_policy_checks(report, config)
    apply_wallet_change_detection(report, state, config)
    (report.get("wallet") or {})["events"] = list(state.get("wallet_events") or [])
    update_whale_confirmed_candidates(state, report, config)
    update_whale_event_state(state, report, config)
    apply_indexer_watchlist(report, state, config)
    update_sdk_subscription_event_state(state, report, config)
    apply_wallet_policy_checks(report, config)
    enrich_operational_fields(report, config, state)
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
    wallet = report.get("wallet") or {}
    if wallet.get("enabled"):
        print(
            "Wallet watch: "
            f"ok={wallet.get('ok')} "
            f"addresses={len(wallet.get('entries') or [])} "
            f"total={format_kas(wallet.get('total_sompi'))} "
            f"detail={wallet.get('detail', 'unknown')}"
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


def failed_check_details(report: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "name": str(check.get("name", "unknown")),
            "detail": str(check.get("detail", "")),
        }
        for check in report.get("checks", [])
        if not check.get("ok")
    ]


def check_failure_causes(report: dict[str, Any]) -> list[str]:
    cause_by_check = {
        "process": "process down",
        "data_dir": "data directory unavailable",
        "rpc_tcp": "RPC unreachable",
        "grpc_metrics": "gRPC metrics unavailable",
        "sync_status": "sync requirement not met",
        "peer_count": "peer count below threshold",
        "active_peer_count": "active peer count below threshold",
        "log_file": "log file missing",
        "log_freshness": "log stale",
        "block_progress": "relay progress stalled",
        "processed_stats_freshness": "processed stats stale",
        "sync_progress": "sync progress stalled",
        "disk_free": "disk free space below threshold",
        "mining_reward_freshness": "mining reward stale",
    }
    causes = []
    for name in failed_check_names(report):
        causes.append(cause_by_check.get(name, name.replace("_", " ")))
    return causes


def health_score(report: dict[str, Any]) -> int:
    score = 100
    for check in report.get("checks", []):
        if check.get("ok"):
            continue
        name = str(check.get("name", "unknown"))
        if name in CRITICAL_CHECKS:
            score -= 30
        elif name in {"disk_free", "log_freshness", "processed_stats_freshness", "block_progress", "sync_progress"}:
            score -= 15
        else:
            score -= 10
    return max(0, min(100, score))


def maintenance_status(config: dict, now: dt.datetime | None = None) -> dict[str, Any]:
    maintenance = config.get("maintenance") or {}
    now = now or dt.datetime.now().astimezone()
    mute_until = parse_iso_datetime(str(maintenance.get("mute_until") or ""))
    enabled = bool(maintenance.get("enabled", False))
    active = enabled
    if mute_until is not None:
        active = active or mute_until > now
    return {
        "active": active,
        "enabled": enabled,
        "mute_until": mute_until.isoformat() if mute_until is not None else "",
        "critical_only": bool(maintenance.get("critical_only", True)),
        "reason": str(maintenance.get("reason") or ""),
    }


def format_maintenance_status(config: dict, now: dt.datetime | None = None) -> str:
    status = maintenance_status(config, now)
    state = "active" if status["active"] else "off"
    until = (status["mute_until"] or "manual") if status["active"] else "none"
    return (
        "maintenance="
        f"{state} "
        f"critical_only={status['critical_only']} "
        f"until={until} "
        f"reason={status['reason'] or 'none'}"
    )


def update_maintenance_config(
    config_path: Path,
    *,
    mute_for_minutes: float | None = None,
    unmute: bool = False,
    critical_only: bool = True,
    reason: str = "",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    raw_config = load_raw_config(config_path)
    maintenance = dict(DEFAULT_CONFIG["maintenance"])
    configured = raw_config.get("maintenance") or {}
    if isinstance(configured, dict):
        maintenance.update(configured)

    if unmute:
        maintenance.update(
            {
                "enabled": False,
                "mute_until": "",
                "reason": "",
            }
        )
    elif mute_for_minutes is not None:
        if mute_for_minutes <= 0:
            raise ValueError("mute minutes must be greater than 0")
        now = now or dt.datetime.now().astimezone()
        mute_until = now + dt.timedelta(minutes=float(mute_for_minutes))
        maintenance.update(
            {
                "enabled": False,
                "mute_until": mute_until.isoformat(timespec="seconds"),
                "critical_only": bool(critical_only),
                "reason": reason,
            }
        )

    raw_config["maintenance"] = maintenance
    save_config(config_path, raw_config)
    merged = dict(DEFAULT_CONFIG)
    merged.update(raw_config)
    return maintenance_status(merged, now)


def update_mining_address_config(
    config_path: Path,
    *,
    address: str | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    if clear:
        address = ""
    elif not looks_like_kaspa_address(address):
        raise ValueError("mining address must look like a Kaspa address, for example kaspa:q...")
    raw_config = load_raw_config(config_path)
    mining = dict(DEFAULT_CONFIG["mining"])
    configured = raw_config.get("mining") or {}
    if isinstance(configured, dict):
        mining.update(configured)
    mining["wallet_address"] = str(address or "").strip()
    raw_config["mining"] = mining
    save_config(config_path, raw_config)
    return mining


def enrich_operational_fields(report: dict[str, Any], config: dict, state: dict[str, Any] | None = None) -> None:
    checked_at = parse_iso_datetime(report.get("checked_at")) or dt.datetime.now().astimezone()
    current_incident = (state or {}).get("current_incident") or {}
    started_at = parse_iso_datetime(current_incident.get("started_at"))
    incident: dict[str, Any] = {
        "active": report.get("status") != "ok",
        "started_at": started_at.isoformat() if started_at is not None else "",
        "duration_seconds": None,
        "failed_checks": failed_check_names(report),
        "causes": check_failure_causes(report),
    }
    if incident["active"]:
        if started_at is None:
            started_at = checked_at
            incident["started_at"] = started_at.isoformat()
        incident["duration_seconds"] = max(0.0, (checked_at - started_at).total_seconds())
    report["incident"] = incident
    report["health_score"] = health_score(report)
    report["failure_causes"] = incident["causes"]
    report["maintenance"] = maintenance_status(config, checked_at)


def update_incident_state(state: dict[str, Any], report: dict[str, Any]) -> str | None:
    checked_at = report.get("checked_at")
    active = report.get("status") != "ok"
    current = state.get("current_incident") or {}
    if active:
        if not current.get("started_at"):
            current = {
                "started_at": checked_at,
                "first_severity": report.get("severity"),
                "first_failed_checks": failed_check_names(report),
            }
        current.update(
            {
                "last_seen_at": checked_at,
                "last_severity": report.get("severity"),
                "last_failed_checks": failed_check_names(report),
                "last_causes": check_failure_causes(report),
            }
        )
        state["current_incident"] = current
        return "incident_opened" if current.get("started_at") == checked_at else "incident_continues"
    if current.get("started_at"):
        state["last_incident"] = {
            **current,
            "resolved_at": checked_at,
            "resolved_severity": report.get("severity"),
        }
        state.pop("current_incident", None)
        return "incident_resolved"
    return None


def alert_muted_by_maintenance(report: dict[str, Any]) -> bool:
    maintenance = report.get("maintenance") or {}
    indexer = report.get("indexer") or {}
    indexer_metrics = indexer.get("metrics") or {}
    if not maintenance.get("active"):
        return False
    if maintenance.get("critical_only", True) and report.get("severity") == "critical":
        return False
    return report.get("status") != "ok"


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


def short_middle(value: Any, *, head: int = 12, tail: int = 8, max_length: int = 28) -> str:
    text = str(value or "")
    if not text:
        return "unknown"
    if len(text) <= max_length:
        return text
    return f"{text[:head]}...{text[-tail:]}"


def format_watch_event_line(event: dict[str, Any], *, default_source: str = "watch") -> str:
    label = str(event.get("label") or "unlabeled")
    source = str(event.get("source") or default_source)
    direction = str(event.get("direction") or event.get("type") or "tx")
    tx_id = short_middle(event.get("tx_id"), head=10, tail=6, max_length=18)
    address = short_middle(event.get("address"), head=12, tail=8, max_length=24)
    amount = format_kas(event.get("amount_sompi"))
    parts = [
        f"- {label}",
        f"source={source}",
        f"direction={direction}",
        f"amount={amount}",
        f"tx={tx_id}",
        f"address={address}",
    ]
    if event.get("transaction_time"):
        parts.append(f"time={event.get('transaction_time')}")
    if event.get("observed_at"):
        parts.append(f"observed={event.get('observed_at')}")
    if event.get("tx_url"):
        parts.append(f"link={event.get('tx_url')}")
    return " ".join(parts)


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
    wallet = report.get("wallet") or {}
    wallet_change = wallet.get("change") or {}
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
        "wallet_ok": wallet.get("ok"),
        "wallet_total_sompi": wallet.get("total_sompi"),
        "wallet_total_kas": wallet.get("total_kas"),
        "wallet_delta_sompi": wallet_change.get("total_delta_sompi"),
        "wallet_delta_kas": wallet_change.get("total_delta_kas"),
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


def html_link(url: Any, label: Any) -> str:
    href = str(url or "").strip()
    text = html.escape(str(label or ""))
    if not href or not re.fullmatch(r"https?://[^\s]+", href):
        return text
    return f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{text}</a>'


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


def fetch_json_url(url: str, timeout: float = 6.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": f"kaspa-node-watchtower/{VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    return json.loads(data.decode("utf-8"))


def indexer_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("indexer") if isinstance(config.get("indexer"), dict) else {}
    return {**DEFAULT_CONFIG["indexer"], **raw}


def join_url(base_url: Any, path: Any) -> str:
    base = str(base_url or "").strip().rstrip("/")
    suffix = str(path or "").strip()
    if not base:
        return ""
    if not suffix:
        return base
    if re.fullmatch(r"https?://[^\s]+", suffix):
        return suffix
    return f"{base}/{suffix.lstrip('/')}"


def find_nested_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in keys:
                return item
        for item in value.values():
            found = find_nested_value(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_nested_value(item, keys)
            if found is not None:
                return found
    return None


def timestamp_age_seconds(value: Any, now: dt.datetime | None = None) -> float | None:
    if value in (None, ""):
        return None
    now = now or dt.datetime.now().astimezone()
    parsed_dt = parse_iso_datetime(str(value)) if isinstance(value, str) else None
    if parsed_dt is not None:
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=now.tzinfo)
        return max(0.0, (now - parsed_dt.astimezone(now.tzinfo)).total_seconds())
    parsed_number = numeric(value)
    if parsed_number is None:
        return None
    if parsed_number > 10_000_000_000:
        parsed_number = parsed_number / 1000
    try:
        parsed_ts = dt.datetime.fromtimestamp(parsed_number, tz=now.tzinfo)
    except (OSError, OverflowError, ValueError):
        return None
    return max(0.0, (now - parsed_ts).total_seconds())


def indexer_payload_ok(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    status = str(payload.get("status") or payload.get("severity") or "").lower()
    if status in {"ok", "healthy", "ready", "up"}:
        return True
    if status in {"alert", "critical", "down", "error", "failed", "unhealthy"}:
        return False
    if "ok" in payload:
        return bool(payload.get("ok"))
    return True


def fetch_indexer_health_payload(url: str, timeout: float) -> tuple[Any, int | None]:
    try:
        return fetch_json_url(url, timeout=timeout), None
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if not body:
            raise
        return json.loads(body.decode("utf-8")), exc.code


def indexer_payload_syncing(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or "").lower()
    if status in {"syncing", "catching_up", "catching-up"}:
        return True
    kaspad = payload.get("kaspad") if isinstance(payload.get("kaspad"), dict) else {}
    kaspad_up = str(kaspad.get("status") or "").lower() in {"up", "ok", "healthy"} or bool(kaspad.get("isSynced"))
    indexer = payload.get("indexer") if isinstance(payload.get("indexer"), dict) else {}
    details = indexer.get("details") if isinstance(indexer.get("details"), list) else []
    reasons = " ".join(
        str(item.get("reason") or "")
        for item in details
        if isinstance(item, dict)
    ).lower()
    has_catchup_reason = "behind" in reasons or "catch" in reasons or "checkpoint" in reasons
    return bool(kaspad_up and has_catchup_reason)


def extract_indexer_metrics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    checkpoint_timestamp = find_nested_value(payload, {"timestamp", "blockTime", "block_time"})
    lag_seconds = find_nested_value(payload, {"lag_seconds", "lagSeconds", "indexer_lag_seconds"})
    schema_version = find_nested_value(payload, {"schema_version", "schemaVersion"})
    version = find_nested_value(payload, {"version", "appVersion", "application_version"})
    return {
        "version": version,
        "schema_version": schema_version,
        "checkpoint_timestamp": checkpoint_timestamp,
        "checkpoint_age_seconds": timestamp_age_seconds(checkpoint_timestamp),
        "lag_seconds": numeric(lag_seconds),
        "payload": payload,
    }


def fetch_optional_indexer_status(config: dict[str, Any]) -> dict[str, Any]:
    cfg = indexer_config(config)
    indexer: dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", False)),
        "configured": bool(str(cfg.get("base_url") or "").strip()),
        "ok": False,
        "health_ok": False,
        "metrics_ok": False,
        "base_url": str(cfg.get("base_url") or "").strip().rstrip("/"),
        "detail": "disabled",
    }
    if not indexer["enabled"]:
        return indexer
    if not indexer["configured"]:
        indexer["detail"] = "no indexer base_url configured"
        return indexer

    timeout = float(cfg.get("timeout_seconds") or DEFAULT_CONFIG["indexer"]["timeout_seconds"])
    health_url = join_url(cfg.get("base_url"), cfg.get("health_path"))
    metrics_url = join_url(cfg.get("base_url"), cfg.get("metrics_path"))
    indexer["health_url"] = health_url
    indexer["metrics_url"] = metrics_url
    start = time.monotonic()
    try:
        health_payload, health_http_status = fetch_indexer_health_payload(health_url, timeout=timeout)
        indexer["health"] = health_payload
        if health_http_status is not None:
            indexer["health_http_status"] = health_http_status
        indexer["health_ok"] = indexer_payload_ok(health_payload)
        indexer["syncing"] = indexer_payload_syncing(health_payload)
        indexer["state"] = "up" if indexer["health_ok"] else ("syncing" if indexer["syncing"] else "down")
        indexer["health_latency_ms"] = round((time.monotonic() - start) * 1000, 1)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        indexer["detail"] = f"health read failed: {exc}"
        indexer["state"] = "down"
        return indexer

    metrics_required = bool(cfg.get("require_metrics", False))
    if metrics_url:
        metrics_start = time.monotonic()
        try:
            metrics_payload = fetch_json_url(metrics_url, timeout=timeout)
            indexer["metrics"] = extract_indexer_metrics(metrics_payload)
            indexer["metrics_ok"] = True
            indexer["metrics_latency_ms"] = round((time.monotonic() - metrics_start) * 1000, 1)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            indexer["metrics_detail"] = f"metrics read failed: {exc}"
            indexer["metrics_ok"] = False
            if metrics_required:
                indexer["detail"] = indexer["metrics_detail"]
                return indexer

    metrics = indexer.get("metrics") or {}
    lag = numeric(metrics.get("lag_seconds"))
    checkpoint_age = numeric(metrics.get("checkpoint_age_seconds"))
    max_lag = float(cfg.get("max_lag_seconds") or DEFAULT_CONFIG["indexer"]["max_lag_seconds"])
    max_checkpoint_age = float(cfg.get("max_checkpoint_age_seconds") or DEFAULT_CONFIG["indexer"]["max_checkpoint_age_seconds"])
    lag_ok = bool(indexer.get("syncing")) or lag is None or lag <= max_lag
    checkpoint_ok = bool(indexer.get("syncing")) or checkpoint_age is None or checkpoint_age <= max_checkpoint_age
    indexer["lag_ok"] = lag_ok
    indexer["checkpoint_fresh"] = checkpoint_ok
    health_acceptable = bool(indexer["health_ok"] or indexer.get("syncing"))
    indexer["ok"] = bool(health_acceptable and (indexer["metrics_ok"] or not metrics_required) and lag_ok and checkpoint_ok)
    lag_text = "unknown" if lag is None else f"{lag:.1f}s"
    checkpoint_text = "unknown" if checkpoint_age is None else f"{checkpoint_age:.1f}s"
    indexer["detail"] = (
        f"state={indexer.get('state', 'unknown')} health_ok={indexer['health_ok']} "
        f"metrics_ok={indexer['metrics_ok']} "
        f"lag={lag_text} checkpoint_age={checkpoint_text}"
    )
    return indexer


def render_indexer_path(template: Any, **params: str) -> str:
    path = str(template or "").strip()
    for key, value in params.items():
        path = path.replace("{" + key + "}", urllib.parse.quote(str(value), safe=""))
    return path


def fetch_indexer_api(config: dict[str, Any], path_template: str, **params: str) -> Any:
    cfg = indexer_config(config)
    if not bool(cfg.get("enabled", False)):
        raise ValueError("indexer is disabled in config")
    base_url = str(cfg.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("indexer base_url is not configured")
    path = render_indexer_path(path_template, **params)
    timeout = float(cfg.get("timeout_seconds") or DEFAULT_CONFIG["indexer"]["timeout_seconds"])
    return fetch_json_url(join_url(base_url, path), timeout=timeout)


def compact_indexer_payload(payload: Any, *, max_items: int = 10) -> str:
    if isinstance(payload, dict):
        parts: list[str] = []
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                text = str(value)
                if len(text) > 80:
                    text = text[:77] + "..."
                parts.append(f"{key}={text}")
            elif isinstance(value, list):
                parts.append(f"{key}_count={len(value)}")
            elif isinstance(value, dict):
                parts.append(f"{key}_fields={len(value)}")
            if len(parts) >= max_items:
                break
        return " ".join(parts) if parts else "empty object"
    if isinstance(payload, list):
        preview = ", ".join(compact_indexer_payload(item, max_items=3) for item in payload[:3])
        return f"items={len(payload)}" + (f" preview=[{preview}]" if preview else "")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 300 else text[:297] + "..."


def format_indexer_api_result(kind: str, identifier: str, payload: Any) -> str:
    return (
        f"Kaspa indexer {kind}: {identifier}\n"
        f"{compact_indexer_payload(payload)}"
    )


def indexer_lookup(config: dict[str, Any], kind: str, value: str) -> int:
    cfg = indexer_config(config)
    value = str(value or "").strip()
    if not value:
        print(f"Kaspa indexer {kind}: missing query value")
        return 2
    path_by_kind = {
        "tx": render_indexer_path(cfg.get("transaction_path"), tx_id=value),
        "address": render_indexer_path(cfg.get("address_transactions_path"), address=value),
        "balance": render_indexer_path(cfg.get("address_balance_path"), address=value),
        "utxos": render_indexer_path(cfg.get("address_utxos_path"), address=value),
        "search": render_indexer_path(cfg.get("search_path"), query=value),
    }
    path = path_by_kind.get(kind)
    if path is None:
        print(f"Kaspa indexer lookup failed: unsupported kind={kind}")
        return 2
    try:
        payload = fetch_indexer_api(config, path)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        print(f"Kaspa indexer {kind}: unavailable; {exc}")
        return 1
    print(format_indexer_api_result(kind, value, payload))
    return 0


def indexer_payload_count(payload: Any, keys: tuple[str, ...]) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def indexer_balance_sompi(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    value = first_present_value(
        payload,
        (
            "balance_sompi",
            "balanceSompi",
            "balance",
            "amount_sompi",
            "amountSompi",
            "total_sompi",
            "totalSompi",
        ),
    )
    if numeric(value) is not None:
        return value
    nested = payload.get("data")
    if isinstance(nested, dict):
        return indexer_balance_sompi(nested)
    return None


def indexer_utxo_count(payload: Any) -> int:
    return indexer_payload_count(payload, ("utxos", "items", "entries", "results", "data"))


def indexer_watch_test(config: dict[str, Any], address: str, label: str = "") -> int:
    address = str(address or "").strip()
    label = str(label or "").strip()
    if not address:
        print("Kaspa indexer watch-test: missing address")
        return 2
    if not looks_like_kaspa_address(address):
        print("Kaspa indexer watch-test: invalid Kaspa address")
        return 2
    cfg = indexer_config(config)
    checks = [
        ("transactions", cfg.get("address_transactions_path"), {"address": address}, ("transactions", "items"), True),
        ("balance", cfg.get("address_balance_path"), {"address": address}, (), False),
        ("utxos", cfg.get("address_utxos_path"), {"address": address}, ("utxos", "items"), False),
    ]
    results: list[tuple[str, str, Any]] = []
    ok = True
    for name, template, params, count_keys, required in checks:
        try:
            payload = fetch_indexer_api(config, render_indexer_path(template, **params))
            if count_keys:
                detail = f"count={indexer_payload_count(payload, count_keys)}"
            else:
                detail = compact_indexer_payload(payload, max_items=4)
            results.append((name, "ok", detail))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            if required:
                ok = False
                results.append((name, "failed", str(exc)))
            else:
                results.append((name, "warn", str(exc)))
    lines = [
        "Kaspa indexer watch-test:",
        f"address={address}",
        f"label={label or 'unlabeled'}",
    ]
    lines.extend(f"{name}={status} {detail}" for name, status, detail in results)
    print("\n".join(lines))
    return 0 if ok else 1


def kas_to_sompi(value: Any) -> int:
    parsed = numeric(value)
    if parsed is None:
        return 0
    return int(round(parsed * 100_000_000))


def indexer_watch_drill(
    config: dict[str, Any],
    address: str = "",
    label: str = "",
    tx_id: str = "",
    amount_kas: Any = 0,
) -> int:
    targets = normalize_watch_addresses(indexer_watch_config(config).get("watch_addresses"))
    address = str(address or "").strip()
    selected = next((item for item in targets if item.get("address") == address), None)
    if not selected and not address and targets:
        selected = targets[0]
        address = selected.get("address", "")
    label = str(label or (selected or {}).get("label") or "drill").strip()
    if not address:
        print("Kaspa indexer watch-drill: missing address")
        return 2
    if not looks_like_kaspa_address(address):
        print("Kaspa indexer watch-drill: invalid Kaspa address")
        return 2

    observed_at = dt.datetime.now().astimezone().isoformat()
    tx_id = str(tx_id or f"drill-{dt.datetime.now().astimezone().strftime('%Y%m%d%H%M%S')}").strip()
    event = {
        "event_key": indexer_watch_event_key(address, tx_id),
        "observed_at": observed_at,
        "type": "drill_indexer_address_tx",
        "source": "indexer_drill",
        "address": address,
        "label": label or "drill",
        "tx_id": tx_id,
        "amount_sompi": kas_to_sompi(amount_kas),
        "amount_kas": numeric(amount_kas) or 0,
        "transaction_time": observed_at,
        "drill": True,
    }

    report, state = build_stateful_report(config)
    watch = report.setdefault("indexer_watch", {})
    events = list(state.get("indexer_watch_events") or [])
    seen = {str(item.get("event_key") or indexer_watch_event_key(item.get("address"), item.get("tx_id"))) for item in events}
    if event["event_key"] in seen:
        watch["events"] = events
        watch["new_events"] = []
        print("Kaspa indexer watch-drill: duplicate event already recorded")
        print(format_watch_event_line(event, default_source="indexer_drill"))
        return 0

    limit = positive_int(
        indexer_watch_config(config).get("event_history_entries"),
        DEFAULT_CONFIG["indexer_watch"]["event_history_entries"],
    )
    events.append(event)
    state["indexer_watch_events"] = events[-limit:]
    watch["watch_addresses"] = list(watch.get("watch_addresses") or targets)
    watch["events"] = list(state["indexer_watch_events"])
    watch["new_events"] = [event]
    watch["ok"] = bool(watch.get("ok", True))
    watch["detail"] = f"drill_event tx={tx_id} total_events={len(watch['events'])}"
    save_state(Path(config.get("state_path") or DEFAULT_CONFIG["state_path"]), state)

    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    history_db_path = sqlite_history_path(config)
    market_snapshot_path = Path(config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"])
    status_page_path = Path(config.get("status_page_path") or DEFAULT_CONFIG["status_page_path"])
    stream_page_path = Path(config.get("stream_page_path") or DEFAULT_CONFIG["stream_page_path"])
    write_status_page(status_page_path, report, state, benchmark_path, recent_recovery_records(config), history_db_path, market_snapshot_path)
    if config.get("canvas_status_page_path") or DEFAULT_CONFIG["canvas_status_page_path"]:
        write_status_page(
            Path(config.get("canvas_status_page_path") or DEFAULT_CONFIG["canvas_status_page_path"]),
            report,
            state,
            benchmark_path,
            recent_recovery_records(config),
            history_db_path,
            market_snapshot_path,
        )
    write_stream_page(stream_page_path, report, state, benchmark_path, market_snapshot_path)
    if config.get("canvas_stream_page_path") or DEFAULT_CONFIG["canvas_stream_page_path"]:
        write_stream_page(
            Path(config.get("canvas_stream_page_path") or DEFAULT_CONFIG["canvas_stream_page_path"]),
            report,
            state,
            benchmark_path,
            market_snapshot_path,
        )
    write_prometheus_metrics(
        Path(config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"]),
        report,
        build_benchmark_summary(benchmark_path, limit=48),
        build_recovery_summary(recovery_history_path(config)),
        build_market_metrics(market_snapshot_path),
    )
    print(format_alert(report, event="indexer_watch_event"))
    return 0


def indexer_watch_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("indexer_watch") if isinstance(config.get("indexer_watch"), dict) else {}
    return {**DEFAULT_CONFIG["indexer_watch"], **raw}


def normalize_watch_addresses(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if isinstance(item, str):
            address = item.strip()
            label = ""
        elif isinstance(item, dict):
            address = str(item.get("address") or "").strip()
            label = str(item.get("label") or "").strip()
        else:
            continue
        if address:
            normalized.append({"address": address, "label": label})
    return normalized


def format_indexer_watchlist(config: dict[str, Any]) -> str:
    watch_config = indexer_watch_config(config)
    targets = normalize_watch_addresses(watch_config.get("watch_addresses"))
    lines = [
        "Kaspa indexer watchlist:",
        f"enabled={bool(watch_config.get('enabled', False))} addresses={len(targets)}",
    ]
    if not targets:
        lines.append("- none")
        return "\n".join(lines)
    for target in targets:
        label = target.get("label") or "unlabeled"
        lines.append(f"- {label}: {target.get('address')}")
    return "\n".join(lines)


def format_indexer_watch_status(report: dict[str, Any]) -> str:
    watch = report.get("indexer_watch") or {}
    address_states = list(watch.get("address_states") or [])
    lines = [
        "Kaspa indexer watchlist:",
        (
            f"enabled={watch.get('enabled', False)} "
            f"ok={watch.get('ok', False)} "
            f"addresses={len(watch.get('watch_addresses') or [])} "
            f"events={len(watch.get('events') or [])} "
            f"new={len(watch.get('new_events') or [])}"
        ),
        f"detail={watch.get('detail', 'unknown')}",
    ]
    if not address_states:
        lines.append("- none")
    for item in address_states:
        lines.append(
            "- "
            f"{item.get('label') or 'unlabeled'}: "
            f"{item.get('address') or 'unknown'} "
            f"ready={bool(item.get('ok'))} "
            f"balance={format_kas(item.get('balance_sompi'))} "
            f"utxos={item.get('utxo_count', 'unknown')} "
            f"txs={item.get('tx_count', 'unknown')} "
            f"last_check={item.get('last_checked_at') or 'unknown'}"
        )
        if item.get("balance_error") or item.get("utxo_error"):
            lines.append(
                "  warnings="
                f"balance={item.get('balance_error') or 'ok'} "
                f"utxos={item.get('utxo_error') or 'ok'}"
            )
    events = list(watch.get("events") or [])
    if events:
        lines.append("recent_events:")
        for event in reversed(events[-5:]):
            lines.append(format_watch_event_line(event, default_source="indexer"))
    else:
        lines.append("recent_events=none")
    return "\n".join(lines)


def watch_readiness_ok(report: dict[str, Any]) -> bool:
    watch = report.get("indexer_watch") or {}
    sdk = report.get("sdk_metrics") or {}
    address_states = list(watch.get("address_states") or [])
    indexer_ready = bool(watch.get("enabled")) and bool(watch.get("ok")) and bool(address_states)
    addresses_ready = all(bool(item.get("ok")) for item in address_states)
    sdk_enabled = bool(sdk.get("enabled"))
    sdk_ready = True
    if sdk_enabled:
        sdk_ready = bool(sdk.get("ok"))
        if sdk.get("subscription_enabled"):
            sdk_ready = sdk_ready and bool(sdk.get("subscription_ok"))
        if sdk.get("subscription_watch_addresses") is not None:
            sdk_ready = sdk_ready and int(sdk.get("subscription_watch_addresses") or 0) >= len(address_states)
    return indexer_ready and addresses_ready and sdk_ready


def format_watch_readiness(report: dict[str, Any]) -> str:
    watch = report.get("indexer_watch") or {}
    sdk = report.get("sdk_metrics") or {}
    address_states = list(watch.get("address_states") or [])
    sdk_targets = {
        str(item.get("address") or "")
        for item in normalize_watch_addresses(sdk.get("subscription_watch_targets"))
        if item.get("address")
    }
    events = list(watch.get("events") or [])
    new_events = list(watch.get("new_events") or [])
    sdk_events = list(sdk.get("events") or [])
    sdk_new_events = list(sdk.get("new_events") or [])
    lines = [
        "Kaspa watch readiness:",
        (
            f"ready={watch_readiness_ok(report)} "
            f"indexer_ok={watch.get('ok', False)} "
            f"sdk_ok={sdk.get('ok', False)} "
            f"addresses={len(address_states)} "
            f"indexer_events={len(events)} "
            f"indexer_new={len(new_events)} "
            f"sdk_watch_events={len(sdk_events)} "
            f"sdk_new={len(sdk_new_events)}"
        ),
        f"indexer_detail={watch.get('detail', 'unknown')}",
        (
            "sdk_subscription="
            f"enabled={sdk.get('subscription_enabled', False)} "
            f"ok={sdk.get('subscription_ok', False)} "
            f"live_events={sdk.get('subscription_events_total', 0)} "
            f"last_event_age={sdk.get('subscription_last_event_age_seconds', 'unknown')}s "
            f"watch_addresses={sdk.get('subscription_watch_addresses', 0)}"
        ),
    ]
    if not address_states:
        lines.append("- none")
    for item in address_states:
        address = str(item.get("address") or "unknown")
        lines.append(
            "- "
            f"{item.get('label') or 'unlabeled'}: "
            f"{address} "
            f"indexer_ready={bool(item.get('ok'))} "
            f"sdk_target={address in sdk_targets if sdk_targets else bool(sdk.get('subscription_watch_addresses'))} "
            f"balance={format_kas(item.get('balance_sompi'))} "
            f"utxos={item.get('utxo_count', 'unknown')} "
            f"txs={item.get('tx_count', 'unknown')} "
            f"last_check={item.get('last_checked_at') or 'unknown'}"
        )
    if new_events or sdk_new_events:
        lines.append("new_events:")
        for event in new_events[-5:]:
            lines.append(format_watch_event_line(event, default_source="indexer"))
        for event in sdk_new_events[-5:]:
            lines.append(format_watch_event_line(event, default_source="sdk_subscription"))
    else:
        lines.append("new_events=none")
    return "\n".join(lines)


def update_indexer_watch_config(
    config_path: Path,
    *,
    add_address: str | None = None,
    remove_address: str | None = None,
    label: str = "",
) -> dict[str, Any]:
    raw_config = load_raw_config(config_path)
    indexer = dict(DEFAULT_CONFIG["indexer"])
    configured_indexer = raw_config.get("indexer") or {}
    if isinstance(configured_indexer, dict):
        indexer.update(configured_indexer)
    watch = dict(DEFAULT_CONFIG["indexer_watch"])
    configured_watch = raw_config.get("indexer_watch") or {}
    if isinstance(configured_watch, dict):
        watch.update(configured_watch)

    targets = normalize_watch_addresses(watch.get("watch_addresses"))
    if add_address is not None:
        address = str(add_address or "").strip()
        if not looks_like_kaspa_address(address):
            raise ValueError("watch address must look like a Kaspa address, for example kaspa:q...")
        label = str(label or "").strip()
        replaced = False
        for target in targets:
            if target["address"] == address:
                target["label"] = label
                replaced = True
                break
        if not replaced:
            targets.append({"address": address, "label": label})
        indexer["enabled"] = True
        watch["enabled"] = True
    elif remove_address is not None:
        address = str(remove_address or "").strip()
        if not address:
            raise ValueError("watch remove requires an address")
        before = len(targets)
        targets = [target for target in targets if target["address"] != address and target.get("label") != address]
        if len(targets) == before:
            raise ValueError(f"watch address not found: {address}")
        watch["enabled"] = bool(targets)

    watch["watch_addresses"] = targets
    raw_config["indexer"] = indexer
    raw_config["indexer_watch"] = watch
    save_config(config_path, raw_config)
    return watch


def first_present_value(value: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in value and value.get(key) not in (None, ""):
            return value.get(key)
    return None


def transaction_items_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("transactions", "items", "results", "entries", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if first_present_value(payload, ("transaction_id", "tx_id", "txid", "id", "hash")) is not None:
        return [payload]
    return []


def indexer_watch_event_key(address: Any, tx_id: Any) -> str:
    return f"{address or ''}|{tx_id or ''}"


def indexer_watch_events_from_payload(
    payload: Any,
    target: dict[str, str],
    checked_at: str,
) -> list[dict[str, Any]]:
    events = []
    address = target.get("address") or ""
    label = target.get("label") or ""
    for item in transaction_items_from_payload(payload):
        tx_id = first_present_value(item, ("transaction_id", "tx_id", "txid", "id", "hash"))
        if tx_id in (None, ""):
            continue
        amount = first_present_value(item, ("amount_sompi", "value_sompi", "amount", "value"))
        accepting_block_hash = first_present_value(item, ("accepting_block_hash", "block_hash", "blockHash"))
        timestamp = first_present_value(item, ("timestamp", "block_time", "blockTime", "time"))
        events.append(
            {
                "event_key": indexer_watch_event_key(address, tx_id),
                "observed_at": checked_at,
                "type": "indexer_address_tx",
                "source": "indexer",
                "address": address,
                "label": label,
                "tx_id": str(tx_id),
                "amount_sompi": amount,
                "amount_kas": kas_from_sompi(amount) if numeric(amount) is not None else None,
                "accepting_block_hash": accepting_block_hash or "",
                "transaction_time": timestamp or "",
            }
        )
    return events


def apply_indexer_watchlist(report: dict[str, Any], state: dict[str, Any], config: dict[str, Any]) -> str | None:
    watch_config = indexer_watch_config(config)
    targets = normalize_watch_addresses(watch_config.get("watch_addresses"))
    watch: dict[str, Any] = {
        "enabled": bool(watch_config.get("enabled", False)),
        "alert_enabled": bool(watch_config.get("alert_enabled", True)),
        "watch_addresses": targets,
        "address_states": [],
        "events": list(state.get("indexer_watch_events") or []),
        "new_events": [],
        "ok": True,
        "detail": "disabled",
    }
    report["indexer_watch"] = watch
    if not watch["enabled"]:
        return None
    if not targets:
        watch["ok"] = False
        watch["detail"] = "no indexer watch addresses configured"
        report["checks"].append(Check("indexer_watchlist", False, watch["detail"]).as_dict())
        recalculate_report_health(report, config)
        return None
    if not bool(indexer_config(config).get("enabled", False)):
        watch["ok"] = False
        watch["detail"] = "indexer is disabled"
        report["checks"].append(Check("indexer_watchlist", False, watch["detail"]).as_dict())
        recalculate_report_health(report, config)
        return None

    checked_at = report.get("checked_at") or dt.datetime.now().astimezone().isoformat()
    candidates = []
    errors = []
    cfg = indexer_config(config)
    for target in targets:
        address_state: dict[str, Any] = {
            "label": target.get("label") or "",
            "address": target["address"],
            "last_checked_at": checked_at,
            "tx_count": None,
            "balance_sompi": None,
            "balance_kas": None,
            "utxo_count": None,
            "ok": True,
            "detail": "ok",
        }
        try:
            payload = fetch_indexer_api(
                config,
                cfg.get("address_transactions_path"),
                address=target["address"],
            )
            address_state["tx_count"] = indexer_payload_count(payload, ("transactions", "items", "entries", "results", "data"))
            candidates.extend(indexer_watch_events_from_payload(payload, target, checked_at))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            address_state["ok"] = False
            address_state["detail"] = f"transactions unavailable: {exc}"
            errors.append(f"{target.get('label') or target['address']}: {exc}")

        try:
            balance_payload = fetch_indexer_api(
                config,
                cfg.get("address_balance_path"),
                address=target["address"],
            )
            balance_sompi = indexer_balance_sompi(balance_payload)
            address_state["balance_sompi"] = balance_sompi
            address_state["balance_kas"] = kas_from_sompi(balance_sompi) if numeric(balance_sompi) is not None else None
            if address_state.get("utxo_count") is None and isinstance(balance_payload, dict):
                utxo_count = first_present_value(balance_payload, ("utxo_count", "utxoCount", "utxos_count", "utxosCount"))
                if numeric(utxo_count) is not None:
                    address_state["utxo_count"] = int(float(utxo_count))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            address_state["balance_error"] = str(exc)

        try:
            utxo_payload = fetch_indexer_api(
                config,
                cfg.get("address_utxos_path"),
                address=target["address"],
            )
            address_state["utxo_count"] = indexer_utxo_count(utxo_payload)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            address_state["utxo_error"] = str(exc)

        detail_parts = []
        if address_state.get("tx_count") is not None:
            detail_parts.append(f"tx={address_state['tx_count']}")
        if address_state.get("balance_sompi") is not None:
            detail_parts.append(f"balance={format_kas(address_state.get('balance_sompi'))}")
        if address_state.get("utxo_count") is not None:
            detail_parts.append(f"utxos={address_state['utxo_count']}")
        if address_state.get("balance_error"):
            detail_parts.append("balance=warn")
        if address_state.get("utxo_error"):
            detail_parts.append("utxos=warn")
        if address_state.get("ok"):
            address_state["detail"] = " ".join(detail_parts) if detail_parts else "ok"
        watch["address_states"].append(address_state)

    limit = positive_int(watch_config.get("event_history_entries"), DEFAULT_CONFIG["indexer_watch"]["event_history_entries"])
    events = list(state.get("indexer_watch_events") or [])
    seen = {str(event.get("event_key") or indexer_watch_event_key(event.get("address"), event.get("tx_id"))) for event in events}
    new_events = []
    for event in candidates:
        key = str(event.get("event_key") or indexer_watch_event_key(event.get("address"), event.get("tx_id")))
        if key in seen:
            continue
        event["event_key"] = key
        seen.add(key)
        new_events.append(event)
    events.extend(new_events)
    state["indexer_watch_events"] = events[-limit:]
    watch["events"] = list(state.get("indexer_watch_events") or [])
    watch["new_events"] = new_events
    watch["ok"] = not errors
    watch["detail"] = (
        f"watched={len(targets)} new_events={len(new_events)} total_events={len(watch['events'])}"
        if not errors
        else "; ".join(errors[:3])
    )
    report["checks"].append(Check("indexer_watchlist", watch["ok"], watch["detail"]).as_dict())
    recalculate_report_health(report, config)
    if new_events and watch["alert_enabled"]:
        return "indexer_watch_event"
    return None


def market_api_list(payload: dict[str, Any], source: str) -> list[Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{source} returned non-object payload")
    ret_code = payload.get("retCode")
    if ret_code not in (None, 0, "0"):
        raise ValueError(str(payload.get("retMsg") or f"{source} API error"))
    rows = ((payload.get("result") or {}).get("list") or [])
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{source} returned no rows")
    return rows


def market_payload_value(payload: Any, path: list[Any]) -> Any:
    current = payload
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list) or len(current) <= part:
                return None
            current = current[part]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
    return current


MARKET_SPOT_PRICE_SOURCES = [
    {
        "source": "Gate",
        "url": "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=KAS_USDT",
        "path": [0, "last"],
    },
    {
        "source": "MEXC",
        "url": "https://api.mexc.com/api/v3/ticker/price?symbol=KASUSDT",
        "path": ["price"],
    },
    {
        "source": "KuCoin",
        "url": "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=KAS-USDT",
        "path": ["data", "price"],
    },
    {
        "source": "Bitget",
        "url": "https://api.bitget.com/api/v2/spot/market/tickers?symbol=KASUSDT",
        "path": ["data", 0, "lastPr"],
    },
    {
        "source": "Kraken",
        "url": "https://api.kraken.com/0/public/Ticker?pair=KASUSD",
        "path": ["result", "*", "c", 0],
    },
    {
        "source": "HTX",
        "url": "https://api.huobi.pro/market/detail/merged?symbol=kasusdt",
        "path": ["tick", "close"],
    },
]


def market_price_from_payload(payload: Any, path: list[Any]) -> float | None:
    if "*" in path:
        wildcard_index = path.index("*")
        prefix = path[:wildcard_index]
        suffix = path[wildcard_index + 1 :]
        parent = market_payload_value(payload, prefix)
        if not isinstance(parent, dict):
            return None
        for value in parent.values():
            price = numeric(market_payload_value(value, suffix))
            if price is not None:
                return price
        return None
    return numeric(market_payload_value(payload, path))


def market_spot_price_dispersion(prices: list[dict[str, Any]], errors: int = 0) -> dict[str, Any]:
    values = sorted(price for item in prices if (price := numeric(item.get("price"))) is not None)
    if not values:
        return {"sources": 0, "errors": errors}
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    low = values[0]
    high = values[-1]
    dispersion_pct = None if median == 0 else ((high - low) / median) * 100
    return {
        "median": median,
        "min": low,
        "max": high,
        "dispersion_pct": dispersion_pct,
        "sources": len(values),
        "errors": errors,
    }


def fetch_market_spot_price_sources(bybit_spot: dict[str, Any], timeout: float = 6.0) -> dict[str, Any]:
    prices = []
    errors = 0
    bybit_price = numeric(bybit_spot.get("lastPrice"))
    if bybit_price is not None:
        prices.append({"source": "Bybit", "price": bybit_price})
    else:
        errors += 1
    for source in MARKET_SPOT_PRICE_SOURCES:
        try:
            payload = fetch_json_url(str(source["url"]), timeout=timeout)
            price = market_price_from_payload(payload, list(source["path"]))
            if price is None:
                errors += 1
                continue
            prices.append({"source": source["source"], "price": price})
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            errors += 1
    return {"prices": prices, "dispersion": market_spot_price_dispersion(prices, errors=errors)}


def format_market_price(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    if abs(parsed) >= 1:
        return f"${parsed:,.2f}"
    return f"${parsed:.5f}"


def format_market_percent(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed * 100:+.2f}%"


def format_market_percent_points(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed:+.2f}%"


def format_market_volume(value: Any, suffix: str = "KAS") -> str:
    text = compact_number(value)
    return "unknown" if text == "unknown" else f"{text} {suffix}"


def format_market_usdt(value: Any) -> str:
    text = compact_number(value)
    return "unknown" if text == "unknown" else f"${text}"


def format_market_time_ms(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    try:
        return dt.datetime.fromtimestamp(parsed / 1000, tz=dt.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return "unknown"


def market_oi_volume_ratio(open_interest: Any, volume_24h: Any) -> float | None:
    oi = numeric(open_interest)
    volume = numeric(volume_24h)
    if oi is None or volume in (None, 0):
        return None
    return oi / volume


def market_funding_z_score(records: list[dict[str, Any]], current_rate: Any, sample_limit: int = 21) -> float | None:
    current = numeric(current_rate)
    if current is None:
        return None
    rates = [
        rate
        for item in records[-sample_limit:]
        if item.get("ok") and (rate := numeric(item.get("futures_funding_rate"))) is not None
    ]
    if len(rates) < 2:
        return None
    mean = sum(rates) / len(rates)
    variance = sum((rate - mean) ** 2 for rate in rates) / len(rates)
    stddev = math.sqrt(variance)
    if stddev == 0:
        return None
    return (current - mean) / stddev


def market_positioning_risk(
    *,
    funding_z_score: Any = None,
    oi_volume_ratio: Any = None,
    basis_pct: Any = None,
    spot_dispersion_pct: Any = None,
) -> dict[str, Any]:
    score = 0
    reasons = []
    funding_z = numeric(funding_z_score)
    if funding_z is not None:
        if abs(funding_z) >= 3:
            score += 2
            reasons.append("funding_z_extreme")
        elif abs(funding_z) >= 2:
            score += 1
            reasons.append("funding_z_elevated")
    oi_volume = numeric(oi_volume_ratio)
    if oi_volume is not None:
        if oi_volume >= 5:
            score += 2
            reasons.append("oi_volume_crowded")
        elif oi_volume >= 3:
            score += 1
            reasons.append("oi_volume_elevated")
    basis = numeric(basis_pct)
    if basis is not None:
        if abs(basis) >= 2:
            score += 2
            reasons.append("basis_extreme")
        elif abs(basis) >= 1:
            score += 1
            reasons.append("basis_elevated")
    dispersion = numeric(spot_dispersion_pct)
    if dispersion is not None:
        if dispersion >= 5:
            score += 2
            reasons.append("spot_dispersion_wide")
        elif dispersion >= 3:
            score += 1
            reasons.append("spot_dispersion_elevated")
    level = "critical" if score >= 4 else ("warning" if score >= 2 else "ok")
    direction = "neutral"
    if (funding_z is not None and funding_z > 0) or (basis is not None and basis > 0):
        direction = "long_crowded"
    if (funding_z is not None and funding_z < 0) or (basis is not None and basis < 0):
        direction = "short_crowded" if direction == "neutral" else "mixed"
    return {
        "score": score,
        "level": level,
        "level_value": {"ok": 0, "warning": 1, "critical": 2}.get(level, -1),
        "direction": direction,
        "reasons": reasons,
        "reason_count": len(reasons),
    }


def format_market_ratio(value: Any, suffix: str = "x") -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed:.2f}{suffix}"


def format_market_zscore(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed:+.2f}sd"


def fetch_market_snapshot(timeout: float = 6.0) -> dict[str, Any]:
    spot_url = "https://api.bybit.com/v5/market/tickers?category=spot&symbol=KASUSDT"
    futures_url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=KASUSDT"
    try:
        spot = market_api_list(fetch_json_url(spot_url, timeout=timeout), "Bybit spot")[0]
        futures = market_api_list(fetch_json_url(futures_url, timeout=timeout), "Bybit linear")[0]
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        return {"ok": False, "source": "Bybit KAS/USDT", "error": str(error)[:180]}

    mark_price = numeric(futures.get("markPrice") or futures.get("lastPrice"))
    index_price = numeric(futures.get("indexPrice"))
    basis_pct = None
    if mark_price is not None and index_price not in (None, 0):
        basis_pct = ((mark_price - index_price) / index_price) * 100
    funding_rate = numeric(futures.get("fundingRate"))
    funding_interval = numeric(futures.get("fundingIntervalHour")) or 8
    funding_apr = None if funding_rate is None else funding_rate * (24 / funding_interval) * 365 * 100
    spot_sources = fetch_market_spot_price_sources(spot, timeout=timeout)
    return {
        "ok": True,
        "source": "Bybit KAS/USDT",
        "spot": {
            "last_price": spot.get("lastPrice"),
            "change_24h": spot.get("price24hPcnt"),
            "volume_24h": spot.get("volume24h"),
            "high_24h": spot.get("highPrice24h"),
            "low_24h": spot.get("lowPrice24h"),
            "price_sources": spot_sources.get("prices") or [],
            "price_dispersion": spot_sources.get("dispersion") or {},
        },
        "futures": {
            "mark_price": futures.get("markPrice") or futures.get("lastPrice"),
            "index_price": futures.get("indexPrice"),
            "basis_pct": basis_pct,
            "funding_rate": futures.get("fundingRate"),
            "funding_apr_pct": funding_apr,
            "next_funding_time": futures.get("nextFundingTime"),
            "open_interest": futures.get("openInterest"),
            "open_interest_value": futures.get("openInterestValue"),
            "volume_24h": futures.get("volume24h"),
            "oi_volume_ratio": market_oi_volume_ratio(futures.get("openInterest"), futures.get("volume24h")),
        },
    }


def market_snapshot_item(snapshot: dict[str, Any], history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    spot = snapshot.get("spot") or {}
    futures = snapshot.get("futures") or {}
    spot_dispersion = spot.get("price_dispersion") or {}
    funding_rate = numeric(futures.get("funding_rate"))
    oi_volume_ratio = numeric(futures.get("oi_volume_ratio"))
    if oi_volume_ratio is None:
        oi_volume_ratio = market_oi_volume_ratio(futures.get("open_interest"), futures.get("volume_24h"))
    funding_z_score = numeric(futures.get("funding_z_score"))
    if funding_z_score is None:
        funding_z_score = market_funding_z_score(history or [], funding_rate)
    risk = market_positioning_risk(
        funding_z_score=funding_z_score,
        oi_volume_ratio=oi_volume_ratio,
        basis_pct=futures.get("basis_pct"),
        spot_dispersion_pct=spot_dispersion.get("dispersion_pct"),
    )
    return {
        "checked_at": dt.datetime.now().astimezone().isoformat(),
        "source": snapshot.get("source", "Bybit KAS/USDT"),
        "ok": bool(snapshot.get("ok")),
        "error": snapshot.get("error"),
        "spot_last_price": numeric(spot.get("last_price")),
        "spot_change_24h": numeric(spot.get("change_24h")),
        "spot_high_24h": numeric(spot.get("high_24h")),
        "spot_low_24h": numeric(spot.get("low_24h")),
        "spot_volume_24h": numeric(spot.get("volume_24h")),
        "spot_price_median": numeric(spot_dispersion.get("median")),
        "spot_price_min": numeric(spot_dispersion.get("min")),
        "spot_price_max": numeric(spot_dispersion.get("max")),
        "spot_price_dispersion_pct": numeric(spot_dispersion.get("dispersion_pct")),
        "spot_price_sources": int(spot_dispersion.get("sources") or 0),
        "spot_price_source_errors": int(spot_dispersion.get("errors") or 0),
        "futures_mark_price": numeric(futures.get("mark_price")),
        "futures_index_price": numeric(futures.get("index_price")),
        "futures_basis_pct": numeric(futures.get("basis_pct")),
        "futures_funding_rate": funding_rate,
        "futures_funding_apr_pct": numeric(futures.get("funding_apr_pct")),
        "futures_funding_z_score": funding_z_score,
        "futures_next_funding_time": futures.get("next_funding_time"),
        "futures_open_interest": numeric(futures.get("open_interest")),
        "futures_open_interest_value": numeric(futures.get("open_interest_value")),
        "futures_volume_24h": numeric(futures.get("volume_24h")),
        "futures_oi_volume_ratio": oi_volume_ratio,
        "market_risk_score": risk["score"],
        "market_risk_level": risk["level"],
        "market_risk_level_value": risk["level_value"],
        "market_risk_direction": risk["direction"],
        "market_risk_reasons": ",".join(risk["reasons"]) if risk["reasons"] else "none",
        "market_risk_reason_count": risk["reason_count"],
    }


def snapshot_from_market_item(item: dict[str, Any]) -> dict[str, Any]:
    if not item.get("ok"):
        return {"ok": False, "source": item.get("source", "Bybit KAS/USDT"), "error": item.get("error")}
    return {
        "ok": True,
        "source": item.get("source", "Bybit KAS/USDT"),
        "spot": {
            "last_price": item.get("spot_last_price"),
            "change_24h": item.get("spot_change_24h"),
            "high_24h": item.get("spot_high_24h"),
            "low_24h": item.get("spot_low_24h"),
            "volume_24h": item.get("spot_volume_24h"),
            "price_dispersion": {
                "median": item.get("spot_price_median"),
                "min": item.get("spot_price_min"),
                "max": item.get("spot_price_max"),
                "dispersion_pct": item.get("spot_price_dispersion_pct"),
                "sources": item.get("spot_price_sources"),
                "errors": item.get("spot_price_source_errors"),
            },
        },
        "futures": {
            "mark_price": item.get("futures_mark_price"),
            "index_price": item.get("futures_index_price"),
            "basis_pct": item.get("futures_basis_pct"),
            "funding_rate": item.get("futures_funding_rate"),
            "funding_apr_pct": item.get("futures_funding_apr_pct"),
            "funding_z_score": item.get("futures_funding_z_score"),
            "next_funding_time": item.get("futures_next_funding_time"),
            "open_interest": item.get("futures_open_interest"),
            "open_interest_value": item.get("futures_open_interest_value"),
            "volume_24h": item.get("futures_volume_24h"),
            "oi_volume_ratio": item.get("futures_oi_volume_ratio"),
            "risk_score": item.get("market_risk_score"),
            "risk_level": item.get("market_risk_level"),
            "risk_level_value": item.get("market_risk_level_value"),
            "risk_direction": item.get("market_risk_direction"),
            "risk_reasons": item.get("market_risk_reasons"),
        },
    }


def format_market_snapshot(snapshot: dict[str, Any]) -> str:
    if not snapshot.get("ok"):
        detail = snapshot.get("error") or "unknown"
        return "\n".join(
            [
                f"Kaspa market snapshot: {snapshot.get('source', 'public market APIs')}",
                f"market_snapshot=unavailable error={detail}",
            ]
        )
    spot = snapshot.get("spot") or {}
    futures = snapshot.get("futures") or {}
    dispersion = spot.get("price_dispersion") or {}
    risk = market_positioning_risk(
        funding_z_score=futures.get("funding_z_score"),
        oi_volume_ratio=futures.get("oi_volume_ratio"),
        basis_pct=futures.get("basis_pct"),
        spot_dispersion_pct=dispersion.get("dispersion_pct"),
    )
    risk_level = futures.get("risk_level") or risk["level"]
    risk_score = futures.get("risk_score") if futures.get("risk_score") is not None else risk["score"]
    risk_direction = futures.get("risk_direction") or risk["direction"]
    risk_reasons = futures.get("risk_reasons") or (",".join(risk["reasons"]) if risk["reasons"] else "none")
    return "\n".join(
        [
            f"Kaspa market snapshot: {snapshot.get('source', 'Bybit KAS/USDT')}",
            (
                "spot="
                f"price={format_market_price(spot.get('last_price'))} "
                f"24h={format_market_percent(spot.get('change_24h'))} "
                f"high={format_market_price(spot.get('high_24h'))} "
                f"low={format_market_price(spot.get('low_24h'))} "
                f"volume={format_market_volume(spot.get('volume_24h'))}"
            ),
            (
                "spot_dispersion="
                f"median={format_market_price(dispersion.get('median'))} "
                f"min={format_market_price(dispersion.get('min'))} "
                f"max={format_market_price(dispersion.get('max'))} "
                f"dispersion={format_market_percent_points(dispersion.get('dispersion_pct'))} "
                f"sources={dispersion.get('sources', 0)} "
                f"errors={dispersion.get('errors', 0)}"
            ),
            (
                "futures="
                f"mark={format_market_price(futures.get('mark_price'))} "
                f"index={format_market_price(futures.get('index_price'))} "
                f"basis={format_market_percent_points(futures.get('basis_pct'))} "
                f"funding={format_market_percent(futures.get('funding_rate'))} "
                f"funding_apr={format_market_percent_points(futures.get('funding_apr_pct'))} "
                f"funding_z={format_market_zscore(futures.get('funding_z_score'))}"
            ),
            (
                "futures_positioning="
                f"open_interest={format_market_volume(futures.get('open_interest'))} "
                f"oi_value={format_market_usdt(futures.get('open_interest_value'))} "
                f"volume_24h={format_market_volume(futures.get('volume_24h'))} "
                f"oi_volume={format_market_ratio(futures.get('oi_volume_ratio'))} "
                f"next_funding={format_market_time_ms(futures.get('next_funding_time'))}"
            ),
            (
                "market_risk="
                f"level={risk_level} "
                f"score={risk_score} "
                f"direction={risk_direction} "
                f"reasons={risk_reasons}"
            ),
        ]
    )


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


BPS_HIGHWAY_THREE_SCRIPT = """
<script type="module">
function renderKaspaCanvasFallback(canvas, payload) {
  const highway = canvas.closest(".bps-highway");
  const ctx = canvas.getContext("2d");
  const laneCount = Math.max(1, Number(payload.laneCount || 20));
  const initialUsage = Math.max(0, Math.min(1, Number(payload.usage || 0)));
  const palette = payload.tone === "critical"
    ? ["#ef4444", "#f97316", "#facc15", "#fb7185"]
    : payload.tone === "warn"
      ? ["#f59e0b", "#fbbf24", "#f97316", "#38bdf8"]
      : ["#34d399", "#67e8f9", "#fbbf24", "#93c5fd"];
  const cars = [];
  for (let lane = 0; lane < laneCount; lane += 1) {
    const lanePressure = 0.65 + (((lane * 7) % laneCount) / laneCount) * 0.7;
    const count = Math.max(1, Math.min(5, 1 + Math.round(initialUsage * 4 * lanePressure)));
    for (let index = 0; index < count; index += 1) {
      cars.push({
        lane,
        z: (index / count + ((lane * 0.037) % 1)) % 1,
        speed: 0.055 + initialUsage * 0.12 + (lane % 4) * 0.004,
        color: palette[(lane + index) % palette.length],
      });
    }
  }

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(320, Math.floor(rect.width * ratio));
    canvas.height = Math.max(210, Math.floor(rect.height * ratio));
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function project(lane, depth, width, height) {
    const horizon = height * 0.02;
    const bottom = height * 1.06;
    const y = horizon + (bottom - horizon) * Math.pow(depth, 1.34);
    const roadTop = width * 0.34;
    const roadBottom = width * 1.18;
    const roadWidth = roadTop + (roadBottom - roadTop) * depth;
    const center = width * 0.64 - width * 0.24 * depth;
    const laneWidth = roadWidth / laneCount;
    const x = center - roadWidth / 2 + laneWidth * (lane + 0.5);
    return { x, y, laneWidth };
  }

  function drawCar(car, width, height) {
    const depth = Math.max(0.03, Math.min(0.99, car.z));
    const p = project(car.lane, depth, width, height);
    const carWidth = Math.max(5, p.laneWidth * 0.72);
    const carHeight = Math.max(8, carWidth * 1.62);
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.rotate(-0.23);
    ctx.shadowColor = car.color;
    ctx.shadowBlur = 10 + depth * 18;
    ctx.fillStyle = car.color;
    ctx.beginPath();
    ctx.roundRect(-carWidth / 2, -carHeight / 2, carWidth, carHeight, Math.max(3, carWidth * 0.18));
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = "rgba(219, 234, 254, 0.88)";
    ctx.beginPath();
    ctx.roundRect(-carWidth * 0.32, -carHeight * 0.24, carWidth * 0.64, carHeight * 0.28, Math.max(2, carWidth * 0.12));
    ctx.fill();
    ctx.fillStyle = "rgba(2, 6, 23, 0.72)";
    ctx.fillRect(-carWidth * 0.58, -carHeight * 0.28, carWidth * 0.12, carHeight * 0.18);
    ctx.fillRect(carWidth * 0.46, -carHeight * 0.28, carWidth * 0.12, carHeight * 0.18);
    ctx.fillRect(-carWidth * 0.58, carHeight * 0.12, carWidth * 0.12, carHeight * 0.18);
    ctx.fillRect(carWidth * 0.46, carHeight * 0.12, carWidth * 0.12, carHeight * 0.18);
    ctx.restore();
  }

  resizeCanvas();
  highway.classList.add("three-ready", "canvas-fallback");
  if (window.ResizeObserver) {
    new ResizeObserver(resizeCanvas).observe(canvas);
  } else {
    window.addEventListener("resize", resizeCanvas);
  }

  let last = performance.now();
  function frame(now) {
    let currentPayload = payload;
    try {
      currentPayload = JSON.parse(canvas.dataset.highwayPayload || "{}");
    } catch (_error) {
      currentPayload = payload;
    }
    const currentUsage = Math.max(0, Math.min(1, Number(currentPayload.usage || 0)));
    const rect = canvas.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;
    const delta = Math.min(0.05, (now - last) / 1000);
    last = now;
    ctx.clearRect(0, 0, width, height);
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, "#111827");
    gradient.addColorStop(1, "#020617");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    const horizon = height * 0.02;
    const bottom = height * 1.05;
    ctx.fillStyle = "#1f2937";
    ctx.beginPath();
    ctx.moveTo(width * 0.64 - width * 0.17, horizon);
    ctx.lineTo(width * 0.64 + width * 0.17, horizon);
    ctx.lineTo(width * 1.02, bottom);
    ctx.lineTo(width * -0.18, bottom);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = "rgba(148, 163, 184, 0.28)";
    ctx.lineWidth = 1;
    for (let lane = 1; lane < laneCount; lane += 1) {
      ctx.beginPath();
      for (let step = 0; step <= 34; step += 1) {
        const depth = step / 34;
        const left = project(lane - 0.5, depth, width, height);
        const right = project(lane + 0.5, depth, width, height);
        const x = (left.x + right.x) / 2;
        const y = left.y;
        if (step === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    cars.forEach((car) => {
      car.speed = 0.055 + currentUsage * 0.12 + (car.lane % 4) * 0.004;
      car.z += car.speed * delta;
      if (car.z > 1) car.z = 0.02;
    });
    cars.slice().sort((a, b) => a.z - b.z).forEach((car) => drawCar(car, width, height));
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

async function renderKaspaBpsHighways() {
  let THREE;
  try {
    if (!window.__kaspaBpsHighwayThreePromise) {
      window.__kaspaBpsHighwayThreePromise = import("https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js");
    }
    THREE = await window.__kaspaBpsHighwayThreePromise;
  } catch (_error) {
    document.querySelectorAll("canvas.bps-highway-canvas").forEach((canvas) => {
      if (canvas.dataset.bpsHighwayInitialized === "1") return;
      canvas.dataset.bpsHighwayInitialized = "1";
      let payload = {};
      try {
        payload = JSON.parse(canvas.dataset.highwayPayload || "{}");
      } catch (_parseError) {
        payload = {};
      }
      renderKaspaCanvasFallback(canvas, payload);
    });
    return;
  }

  document.querySelectorAll("canvas.bps-highway-canvas").forEach((canvas) => {
    if (canvas.dataset.bpsHighwayInitialized === "1") return;
    canvas.dataset.bpsHighwayInitialized = "1";
    const highway = canvas.closest(".bps-highway");
    let payload = {};
    try {
      payload = JSON.parse(canvas.dataset.highwayPayload || "{}");
    } catch (_error) {
      payload = {};
    }
    const laneCount = Math.max(1, Number(payload.laneCount || 20));
    const usage = Math.max(0, Math.min(1, Number(payload.usage || 0)));
    const laneWidth = 1.32;
    const roadWidth = laneCount * laneWidth;
    const roadLength = 92;
    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog(0x07111f, 42, 120);

    const camera = new THREE.PerspectiveCamera(34, 16 / 9, 0.1, 190);
    camera.position.set(8.6, 5.4, 3.8);
    camera.lookAt(-4.4, -3.4, -22);

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    scene.add(new THREE.HemisphereLight(0xe0f2fe, 0x0f172a, 1.45));
    const key = new THREE.DirectionalLight(0xffffff, 2.0);
    key.position.set(-8, 18, 16);
    key.castShadow = true;
    scene.add(key);

    const roadMaterial = new THREE.MeshStandardMaterial({ color: 0x182230, roughness: 0.82, metalness: 0.05 });
    const road = new THREE.Mesh(new THREE.BoxGeometry(roadWidth + 2.4, 0.18, roadLength), roadMaterial);
    road.position.set(0, -0.12, -27);
    road.receiveShadow = true;
    scene.add(road);

    const shoulderMaterial = new THREE.MeshStandardMaterial({ color: 0x0f766e, roughness: 0.72, metalness: 0.05 });
    [-1, 1].forEach((side) => {
      const shoulder = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.05, roadLength), shoulderMaterial);
      shoulder.position.set(side * (roadWidth / 2 + 0.72), 0.03, -27);
      shoulder.receiveShadow = true;
      scene.add(shoulder);
    });

    const lineMaterial = new THREE.MeshStandardMaterial({ color: 0xe5edf6, emissive: 0x334155, roughness: 0.45 });
    for (let lane = 1; lane < laneCount; lane += 1) {
      const x = -roadWidth / 2 + lane * laneWidth;
      for (let index = 0; index < 22; index += 1) {
        const dash = new THREE.Mesh(new THREE.BoxGeometry(0.052, 0.045, 2.05), lineMaterial);
        dash.position.set(x, 0.04, -70 + index * 4.25);
        scene.add(dash);
      }
    }

    const colorByTone = {
      ok: [0x39d98a, 0x67e8f9, 0xfbbf24, 0x93c5fd],
      warn: [0xf59e0b, 0xfbbf24, 0xf97316, 0x38bdf8],
      critical: [0xef4444, 0xf97316, 0xfacc15, 0xfb7185],
    };
    const palette = colorByTone[payload.tone] || colorByTone.ok;
    const bodyGeometry = new THREE.BoxGeometry(0.86, 0.32, 1.58);
    const cabinGeometry = new THREE.BoxGeometry(0.58, 0.27, 0.66);
    const wheelGeometry = new THREE.CylinderGeometry(0.13, 0.13, 0.12, 10);
    const wheelMaterial = new THREE.MeshStandardMaterial({ color: 0x05070a, roughness: 0.56 });
    const cars = [];

    function makeCar(color) {
      const car = new THREE.Group();
      const bodyMaterial = new THREE.MeshStandardMaterial({ color, roughness: 0.36, metalness: 0.12 });
      const glassMaterial = new THREE.MeshStandardMaterial({ color: 0xbae6fd, emissive: 0x0e7490, emissiveIntensity: 0.18, roughness: 0.18, metalness: 0.05 });
      const body = new THREE.Mesh(bodyGeometry, bodyMaterial);
      body.position.y = 0.28;
      body.castShadow = true;
      car.add(body);
      const cabin = new THREE.Mesh(cabinGeometry, glassMaterial);
      cabin.position.set(0, 0.55, -0.16);
      cabin.castShadow = true;
      car.add(cabin);
      [[-0.42, -0.43], [0.42, -0.43], [-0.42, 0.43], [0.42, 0.43]].forEach(([x, z]) => {
        const wheel = new THREE.Mesh(wheelGeometry, wheelMaterial);
        wheel.rotation.z = Math.PI / 2;
        wheel.position.set(x, 0.18, z);
        wheel.castShadow = true;
        car.add(wheel);
      });
      return car;
    }

    for (let lane = 0; lane < laneCount; lane += 1) {
      const lanePressure = 0.65 + (((lane * 7) % laneCount) / laneCount) * 0.7;
      const count = Math.max(1, Math.min(5, 1 + Math.round(usage * 4 * lanePressure)));
      const x = -roadWidth / 2 + laneWidth / 2 + lane * laneWidth;
      for (let index = 0; index < count; index += 1) {
        const car = makeCar(palette[(lane + index) % palette.length]);
        const spread = roadLength / count;
        car.position.set(x, 0.08, 22 - index * spread - ((lane * 2.7) % 11));
        car.userData.speed = 9.5 + usage * 19 + (lane % 4) * 0.7;
        scene.add(car);
        cars.push(car);
      }
    }

    function resize() {
      const rect = canvas.getBoundingClientRect();
      const width = Math.max(320, Math.floor(rect.width));
      const height = Math.max(210, Math.floor(rect.height));
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }
    resize();
    highway.classList.add("three-ready");
    if (window.ResizeObserver) {
      new ResizeObserver(resize).observe(canvas);
    } else {
      window.addEventListener("resize", resize);
    }

    let last = performance.now();
    function animate(now) {
      let currentPayload = payload;
      try {
        currentPayload = JSON.parse(canvas.dataset.highwayPayload || "{}");
      } catch (_error) {
        currentPayload = payload;
      }
      const currentUsage = Math.max(0, Math.min(1, Number(currentPayload.usage || 0)));
      const delta = Math.min(0.05, (now - last) / 1000);
      last = now;
      cars.forEach((car) => {
        car.userData.speed = 9.5 + currentUsage * 19 + (Math.abs(car.position.x / laneWidth) % 4) * 0.7;
        car.position.z += car.userData.speed * delta;
        if (car.position.z > 31) {
          car.position.z = -72 - Math.random() * 16;
        }
      });
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    }
    requestAnimationFrame(animate);
  });
}

renderKaspaBpsHighways();
window.renderKaspaBpsHighways = renderKaspaBpsHighways;
</script>
"""


def bps_highway_visual(
    blocks_per_second: Any,
    *,
    transactions_per_second: Any = None,
    sample_age_seconds: Any = None,
    sample_window_seconds: Any = None,
    transaction_count: Any = None,
    lane_count: int = 20,
    capacity_bps: float = 20.0,
) -> str:
    rate = numeric(blocks_per_second) or 0.0
    tx_rate = numeric(transactions_per_second)
    sample_age = numeric(sample_age_seconds)
    usage = max(0.0, min(1.0, rate / capacity_bps if capacity_bps > 0 else 0.0))
    if usage >= 0.9:
        tone = "critical"
        state = "near capacity"
    elif usage >= 0.7:
        tone = "warn"
        state = "heavy flow"
    else:
        tone = "ok"
        state = "clear flow"

    active_lanes = lane_count if rate > 0 else 0
    speed_seconds = max(1.8, 8.0 - usage * 5.2)
    lanes = []
    for lane in range(lane_count):
        lane_active = lane < active_lanes
        car_count = 0
        if lane_active:
            lane_pressure = 0.65 + (((lane * 7) % lane_count) / max(1, lane_count)) * 0.7
            car_count = 1 + int(round(usage * 4 * lane_pressure))
            car_count = max(1, min(5, car_count))
        cars = []
        for car in range(car_count):
            delay = -1 * ((lane * 0.37) + (car * speed_seconds / max(1, car_count)))
            width = 22 + ((lane + car) % 3) * 5
            cars.append(
                '<span class="bps-car" '
                f'style="--delay: {delay:.2f}s; --speed: {speed_seconds + (lane % 4) * 0.18:.2f}s; '
                f'--car-width: {width}px;"></span>'
            )
        lanes.append(
            '<div class="bps-lane'
            + (" active" if lane_active else "")
            + f'" aria-label="lane {lane + 1}">'
            + "".join(cars)
            + "</div>"
        )

    tx_text = "unknown tx/s" if tx_rate is None else f"{tx_rate:.1f} tx/s"
    age_text = "unknown age" if sample_age is None else f"{sample_age:g}s old"
    window_text = "unknown window" if sample_window_seconds is None else f"{sample_window_seconds}s window"
    tx_count_text = "unknown tx" if transaction_count is None else f"{transaction_count} tx"
    payload = html.escape(
        json.dumps(
            {
                "rate": rate,
                "usage": usage,
                "laneCount": lane_count,
                "tone": tone,
            },
            separators=(",", ":"),
        ),
        quote=True,
    )

    return f"""
<div class="bps-highway {html.escape(tone)}" data-bps-highway role="img" aria-label="20 lane BPS highway showing block throughput">
  <div class="bps-highway-head">
    <div>
      <div class="bps-kicker">20-Lane BPS Highway</div>
      <div class="bps-rate" data-bps-rate>{html.escape(f"{rate:.1f} BPS")}</div>
    </div>
    <div class="bps-state"><span data-bps-state>{html.escape(state)} · {usage * 100:.0f}%</span><br><span data-bps-tx-rate>{html.escape(tx_text)}</span></div>
  </div>
  <canvas class="bps-highway-canvas" data-highway-payload="{payload}" aria-label="3D highway with cars flowing by BPS"></canvas>
  <div class="bps-road">{''.join(lanes)}</div>
  <div class="bps-caption" data-bps-caption>{html.escape(str(active_lanes))}/{lane_count} lanes open · rusty-kaspa processed-stats log · {html.escape(age_text)} · {html.escape(tx_count_text)} / {html.escape(window_text)}</div>
</div>
""" + BPS_HIGHWAY_THREE_SCRIPT


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
        "active_peer_count": "Treat inactive peers as connectivity loss; inspect firewall, NAT, and kaspad peer state.",
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


def stream_metric(label: str, value: Any, detail: str = "", tone: str = "neutral") -> str:
    return f"""<article class="stream-metric {html.escape(tone)}">
  <div class="metric-label">{html.escape(str(label))}</div>
  <div class="metric-value">{html.escape(str(value))}</div>
  <div class="metric-detail">{html.escape(str(detail))}</div>
</article>"""


def write_stream_page(
    path: Path,
    report: dict[str, Any],
    state: dict[str, Any],
    benchmark_path: Path | None = None,
    market_snapshot_path: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    grpc_metrics = report.get("grpc_metrics") or {}
    progress = report.get("progress") or {}
    sync_progress = report.get("sync_progress") or {}
    disk = report.get("disk") or {}
    wallet = report.get("wallet") or {}
    mining_status = report.get("mining") or {}
    whale = report.get("whale_watch") or {}
    indexer = report.get("indexer") or {}
    indexer_metrics = indexer.get("metrics") or {}
    indexer_watch = report.get("indexer_watch") or {}
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    history_items = state.get("history", [])[-80:]
    latest_processed = progress.get("latest_processed") or {}
    latest_processed_age = progress.get("latest_processed_age_seconds")
    latest_processed_age_text = "unknown" if latest_processed_age is None else f"{latest_processed_age}s old"
    transaction_rate = latest_processed.get("transactions_per_second")
    transaction_rate_text = "unknown" if transaction_rate is None else f"{float(transaction_rate):.1f}/s"
    processed_rate = latest_processed.get("blocks_per_second")
    processed_rate_text = "unknown" if processed_rate is None else f"{float(processed_rate):.1f}/s"
    mempool_history = history_items + [
        {
            "checked_at": report.get("checked_at"),
            "mempool_size": grpc_metrics.get("mempool_size"),
        }
    ]
    mempool_chart = mempool_10s_chart(mempool_history)
    transaction_chart = transaction_rate_chart(progress.get("processed_samples") or [])
    processed_chart = processed_rate_chart(progress.get("processed_samples") or [])
    bps_highway = bps_highway_visual(
        processed_rate,
        transactions_per_second=transaction_rate,
        sample_age_seconds=latest_processed_age,
        sample_window_seconds=latest_processed.get("seconds"),
        transaction_count=latest_processed.get("transactions"),
    )
    relay_chart = relay_intake_chart(progress.get("relay_samples") or [])
    peer_chart = sparkline_svg(
        history_items + [{"peer_count": grpc_metrics.get("peer_count")}],
        "peer_count",
        "#6ee7b7",
    )
    daa_chart = sparkline_svg(
        history_items + [{"virtual_daa_score": grpc_metrics.get("virtual_daa_score")}],
        "virtual_daa_score",
        "#93c5fd",
    )
    severity_chart = severity_timeline(history_items)
    benchmark_summary = build_benchmark_summary(
        benchmark_path or Path(DEFAULT_CONFIG["benchmark_path"]),
        limit=48,
    )
    market_metrics = build_market_metrics(market_snapshot_path or Path(DEFAULT_CONFIG["market_snapshot_path"]))
    latest_market = market_metrics.get("latest_successful") or {}
    market_snapshot = snapshot_from_market_item(latest_market) if latest_market else {}
    spot = market_snapshot.get("spot") or {}
    futures = market_snapshot.get("futures") or {}
    market_checked = latest_market.get("checked_at") or market_metrics.get("last_checked_at") or "snapshot pending"
    failed = failed_check_names(report)
    failure_text = ", ".join(failed) if failed else "None"
    last_alert_at = state.get("last_alert_at") or "None"
    sync_text = "synced" if grpc_metrics.get("is_synced") is True else str(grpc_metrics.get("is_synced", "unknown"))
    severity = str(report.get("severity", "unknown"))
    checked_at = str(report.get("checked_at") or "unknown")
    node_name = str(report.get("node_name") or "kaspa-node")
    network_id = str(grpc_metrics.get("network_id") or "unknown")
    recovery = report.get("recovery") or {}

    overview_metrics = "\n".join(
        [
            stream_metric("Severity", severity.upper(), failure_text, severity if severity in {"ok", "warn", "critical"} else "neutral"),
            stream_metric("Network", network_id, f"node {node_name}", "neutral"),
            stream_metric("Sync", sync_text, f"DAA {compact_number(grpc_metrics.get('virtual_daa_score'))}", tone_for_check(report, "sync_status")),
            stream_metric("Peers", grpc_metrics.get("peer_count", "unknown"), f"active {grpc_metrics.get('active_peers', 'unknown')}", tone_for_check(report, "peer_count")),
            stream_metric("Tx Rate", transaction_rate_text, latest_processed_age_text, tone_for_check(report, "processed_stats_freshness")),
            stream_metric("Mempool", compact_number(grpc_metrics.get("mempool_size")), "10-second buckets", "neutral"),
            stream_metric("Hashrate", format_hashrate(grpc_metrics.get("network_hashes_per_second")), f"window {grpc_metrics.get('network_hashrate_window_size', 'unknown')}", "neutral"),
            stream_metric("Disk Free", format_gib(disk.get("free_gb")), f"{disk.get('free_percent', 'unknown')}% free", tone_for_check(report, "disk_free")),
        ]
    )
    network_metrics = "\n".join(
        [
            stream_metric("Hashrate", format_hashrate(grpc_metrics.get("network_hashes_per_second")), f"window {grpc_metrics.get('network_hashrate_window_size', 'unknown')}", "neutral"),
            stream_metric("Relay", progress.get("relay_blocks_in_window", 0), f"{progress.get('relay_events_in_window', 0)} events / {progress.get('window_minutes', 'unknown')}m", tone_for_check(report, "block_progress")),
            stream_metric("Tips", grpc_metrics.get("tip_count", "unknown"), f"pruning {short_hash(grpc_metrics.get('pruning_point_hash'))}", "neutral"),
            stream_metric("Disk", format_gib(disk.get("free_gb")), f"{disk.get('free_percent', 'unknown')}% free", tone_for_check(report, "disk_free")),
        ]
    )
    throughput_metrics = "\n".join(
        [
            stream_metric("Transactions", transaction_rate_text, f"{latest_processed.get('transactions', 'unknown')} tx / {latest_processed.get('seconds', 'unknown')}s", tone_for_check(report, "processed_stats_freshness")),
            stream_metric("Blocks", processed_rate_text, f"{latest_processed.get('blocks', 'unknown')} blocks / {latest_processed.get('seconds', 'unknown')}s", tone_for_check(report, "processed_stats_freshness")),
            stream_metric("Freshness", latest_processed_age_text, "processed-stats age", tone_for_check(report, "processed_stats_freshness")),
            stream_metric("Relay Window", progress.get("relay_blocks_in_window", 0), f"{progress.get('relay_events_in_window', 0)} events / {progress.get('window_minutes', 'unknown')}m", tone_for_check(report, "block_progress")),
        ]
    )
    market_metrics_html = "\n".join(
        [
            stream_metric("Spot", format_market_price(spot.get("last_price")), f"24h {format_market_percent(spot.get('change_24h'))}", "neutral"),
            stream_metric("Spot Volume", format_market_volume(spot.get("volume_24h")), f"source {market_metrics.get('source', 'unknown')}", "neutral"),
            stream_metric("24h High", format_market_price(spot.get("high_24h")), "spot range", "neutral"),
            stream_metric("24h Low", format_market_price(spot.get("low_24h")), "spot range", "neutral"),
            stream_metric("Mark", format_market_price(futures.get("mark_price")), f"index {format_market_price(futures.get('index_price'))}", "neutral"),
            stream_metric("Basis", format_market_percent_points(futures.get("basis_pct")), "mark vs index", "neutral"),
            stream_metric("Funding", format_market_percent(futures.get("funding_rate")), f"APR {format_market_percent_points(futures.get('funding_apr_pct'))}", "neutral"),
            stream_metric("Market Snapshots", market_metrics.get("successful_snapshots", 0), f"{market_metrics.get('snapshots', 0)} total", "neutral"),
        ]
    )
    futures_metrics_html = "\n".join(
        [
            stream_metric("Mark", format_market_price(futures.get("mark_price")), f"index {format_market_price(futures.get('index_price'))}", "neutral"),
            stream_metric("Basis", format_market_percent_points(futures.get("basis_pct")), "mark vs index", "neutral"),
            stream_metric("Funding", format_market_percent(futures.get("funding_rate")), f"APR {format_market_percent_points(futures.get('funding_apr_pct'))}", "neutral"),
            stream_metric("Open Interest", format_market_volume(futures.get("open_interest")), format_market_usdt(futures.get("open_interest_value")), "neutral"),
            stream_metric("Futures Volume", format_market_volume(futures.get("volume_24h")), "24h Bybit linear", "neutral"),
            stream_metric("Next Funding", format_market_time_ms(futures.get("next_funding_time")), "UTC", "neutral"),
            stream_metric("Benchmark OK", format_ratio(benchmark_summary.get("ok_ratio")), f"{benchmark_summary.get('snapshots')} snapshots", "neutral"),
            stream_metric("Recovery", recovery.get("action", "unknown"), recovery.get("mode", "manual"), "neutral"),
        ]
    )

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kaspa Watchtower Stream</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; width: 100%; height: 100%; background: #030712; overflow: hidden; }}
    body {{ color: #e5edf6; }}
    .stream-stage {{ position: fixed; left: 50%; top: 50%; width: 1920px; height: 1080px; transform-origin: center; background: #07111f; overflow: hidden; }}
    .stream-stage::before {{ content: ""; position: absolute; inset: 0; background: radial-gradient(circle at 18% 18%, rgba(20, 125, 100, 0.24), transparent 36%), linear-gradient(135deg, rgba(15, 23, 42, 0.94), rgba(4, 10, 19, 0.98)); }}
    .stream-scene {{ position: absolute; inset: 0; display: none; padding: 46px 62px 44px; grid-template-rows: auto 1fr auto; gap: 22px; }}
    .stream-scene.active {{ display: grid; }}
    .scene-head {{ position: relative; display: flex; justify-content: space-between; align-items: flex-start; gap: 32px; z-index: 1; }}
    .scene-kicker {{ color: #8bd8c2; font-size: 20px; font-weight: 800; letter-spacing: 0; text-transform: uppercase; }}
    h1 {{ margin: 6px 0 0; font-size: 56px; line-height: 0.98; letter-spacing: 0; }}
    .scene-meta {{ text-align: right; color: #a6b7c8; font-size: 20px; font-weight: 700; line-height: 1.38; }}
    .scene-body {{ position: relative; z-index: 1; display: grid; gap: 20px; min-height: 0; }}
    .grid-2 {{ grid-template-columns: 1fr 1fr; align-items: stretch; }}
    .grid-main {{ grid-template-columns: 0.9fr 1.1fr; align-items: stretch; }}
    .metrics-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }}
    .metrics-grid.compact {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .stream-metric {{ min-height: 134px; padding: 18px 20px; border: 1px solid rgba(148, 163, 184, 0.22); background: rgba(15, 23, 42, 0.78); border-radius: 8px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05); }}
    .stream-metric.ok {{ border-color: rgba(52, 211, 153, 0.48); }}
    .stream-metric.warn {{ border-color: rgba(251, 191, 36, 0.60); }}
    .stream-metric.critical {{ border-color: rgba(248, 113, 113, 0.70); }}
    .metric-label {{ color: #90a4b8; font-size: 18px; font-weight: 800; text-transform: uppercase; }}
    .metric-value {{ margin-top: 10px; color: #f8fafc; font-size: 40px; line-height: 0.98; font-weight: 900; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .metric-detail {{ margin-top: 12px; color: #a6b7c8; font-size: 18px; font-weight: 700; line-height: 1.25; }}
    .chart-panel {{ padding: 22px; border: 1px solid rgba(148, 163, 184, 0.22); border-radius: 8px; background: rgba(8, 17, 31, 0.78); min-height: 0; }}
    .chart-title {{ margin-bottom: 14px; color: #d6e2ef; font-size: 22px; font-weight: 900; }}
    .processed-chart, .sparkline {{ width: 100%; height: 270px; display: block; background: rgba(2, 6, 23, 0.55); border-radius: 8px; }}
    .sparkline {{ height: 178px; }}
    .bps-highway {{ display: grid; grid-template-rows: auto 1fr auto; gap: 16px; min-height: 360px; padding: 22px; border: 1px solid rgba(148, 163, 184, 0.24); border-radius: 8px; background: rgba(2, 6, 23, 0.62); overflow: hidden; }}
    .bps-highway-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 22px; }}
    .bps-kicker {{ color: #8bd8c2; font-size: 18px; font-weight: 900; text-transform: uppercase; }}
    .bps-rate {{ margin-top: 4px; color: #f8fafc; font-size: 54px; line-height: 0.95; font-weight: 950; }}
    .bps-state {{ color: #d6e2ef; font-size: 20px; font-weight: 900; text-align: right; }}
    .bps-state span {{ color: #a6b7c8; font-size: 17px; font-weight: 800; }}
    .bps-highway-canvas {{ width: 100%; height: 420px; display: block; border-radius: 8px; background: linear-gradient(180deg, rgba(15, 23, 42, 0.96), rgba(2, 6, 23, 0.98)); }}
    .bps-highway.three-ready .bps-road {{ display: none; }}
    .bps-highway.three-fallback .bps-highway-canvas {{ display: none; }}
    .bps-road {{ display: grid; grid-template-rows: repeat(20, 1fr); gap: 4px; min-height: 0; padding: 10px 0; }}
    .bps-lane {{ position: relative; overflow: hidden; border-top: 1px dashed rgba(148, 163, 184, 0.22); background: linear-gradient(90deg, rgba(15, 23, 42, 0.18), rgba(15, 23, 42, 0.58)); border-radius: 3px; }}
    .bps-lane.active {{ background: linear-gradient(90deg, rgba(20, 125, 100, 0.20), rgba(15, 23, 42, 0.68)); }}
    .bps-car {{ position: absolute; top: 50%; left: -46px; width: var(--car-width); height: 8px; border-radius: 999px; background: #6ee7b7; box-shadow: 0 0 12px rgba(110, 231, 183, 0.55); transform: translateY(-50%); animation: bps-drive var(--speed) linear infinite; animation-delay: var(--delay); }}
    .bps-highway.warn .bps-car {{ background: #fbbf24; box-shadow: 0 0 12px rgba(251, 191, 36, 0.55); }}
    .bps-highway.critical .bps-car {{ background: #f87171; box-shadow: 0 0 12px rgba(248, 113, 113, 0.60); }}
    .bps-caption {{ color: #a6b7c8; font-size: 18px; font-weight: 800; }}
    @keyframes bps-drive {{ from {{ transform: translate(-50px, -50%); }} to {{ transform: translate(1980px, -50%); }} }}
    .empty-chart {{ display: grid; place-items: center; min-height: 178px; color: #93a4b8; border: 1px dashed rgba(148, 163, 184, 0.32); border-radius: 8px; font-size: 22px; font-weight: 800; }}
    .severity-timeline {{ height: 62px; display: flex; gap: 6px; align-items: stretch; }}
    .severity-segment {{ flex: 1; border-radius: 3px; background: #64748b; }}
    .severity-segment.ok {{ background: #34d399; }}
    .severity-segment.warn {{ background: #fbbf24; }}
    .severity-segment.critical {{ background: #f87171; }}
    .hero-value {{ align-self: center; padding: 36px; border-radius: 8px; border: 1px solid rgba(110, 231, 183, 0.42); background: rgba(6, 78, 59, 0.24); }}
    .hero-value .label {{ color: #9fe8d4; font-size: 23px; font-weight: 900; text-transform: uppercase; }}
    .hero-value .value {{ margin-top: 14px; color: #f8fafc; font-size: 88px; line-height: 0.9; font-weight: 950; }}
    .hero-value .detail {{ margin-top: 18px; color: #b7c8d8; font-size: 23px; font-weight: 700; }}
    .scene-foot {{ position: relative; z-index: 1; display: flex; align-items: center; justify-content: space-between; color: #91a4b8; font-size: 18px; font-weight: 800; }}
    .scene-dots {{ display: flex; gap: 10px; }}
    .scene-dot {{ width: 42px; height: 6px; border-radius: 999px; background: rgba(148, 163, 184, 0.35); }}
    .scene-dot.active {{ background: #6ee7b7; }}
    .progress {{ position: absolute; left: 0; right: 0; bottom: 0; height: 8px; background: rgba(148, 163, 184, 0.18); }}
    .progress > span {{ display: block; width: 0%; height: 100%; background: #6ee7b7; }}
  </style>
</head>
<body>
  <main id="stream-stage" class="stream-stage" data-stream-width="1920" data-stream-height="1080" data-default-interval-ms="5000">
    <section class="stream-scene active" data-scene="overall">
      <header class="scene-head"><div><div class="scene-kicker">Kaspa Watchtower</div><h1>Overall Status</h1></div><div class="scene-meta">{html.escape(node_name)}<br>{html.escape(checked_at)}</div></header>
      <div class="scene-body"><div class="metrics-grid">{overview_metrics}</div><div class="chart-panel"><div class="chart-title">Recent Severity</div>{severity_chart}</div></div>
      <footer class="scene-foot"><span>Failed checks: {html.escape(failure_text)}</span><span data-scene-label></span></footer>
    </section>
    <section class="stream-scene" data-scene="network">
      <header class="scene-head"><div><div class="scene-kicker">Network</div><h1>Network Health</h1></div><div class="scene-meta">sync {html.escape(sync_text)}<br>network {html.escape(network_id)}</div></header>
      <div class="scene-body grid-main"><div class="metrics-grid compact">{network_metrics}</div><div class="chart-panel"><div class="chart-title">Relay Intake</div>{relay_chart}</div><div class="chart-panel"><div class="chart-title">Peer Trend</div>{peer_chart}</div><div class="chart-panel"><div class="chart-title">DAA Trend</div>{daa_chart}</div></div>
      <footer class="scene-foot"><span>Sync progress: DAA {html.escape(compact_number(sync_progress.get('daa_delta')))} delta</span><span data-scene-label></span></footer>
    </section>
    <section class="stream-scene" data-scene="throughput">
      <header class="scene-head"><div><div class="scene-kicker">Throughput</div><h1>Transaction Throughput</h1></div><div class="scene-meta">processed stats<br>{html.escape(latest_processed_age_text)}</div></header>
      <div class="scene-body grid-main"><div class="hero-value"><div class="label">Live Tx Rate</div><div class="value">{html.escape(transaction_rate_text)}</div><div class="detail">{html.escape(str(latest_processed.get('transactions', 'unknown')))} tx over {html.escape(str(latest_processed.get('seconds', 'unknown')))}s</div></div><div class="chart-panel"><div class="chart-title">BPS Highway</div>{bps_highway}</div><div class="metrics-grid compact">{throughput_metrics}</div><div class="chart-panel"><div class="chart-title">Blocks Per Second</div>{processed_chart}</div></div>
      <footer class="scene-foot"><span>Processed freshness check: {html.escape('OK' if check_passed(report, 'processed_stats_freshness') else 'FAIL')}</span><span data-scene-label></span></footer>
    </section>
    <section class="stream-scene" data-scene="mempool">
      <header class="scene-head"><div><div class="scene-kicker">Mempool</div><h1>Mempool Activity</h1></div><div class="scene-meta">10-second bars<br>latest {html.escape(compact_number(grpc_metrics.get('mempool_size')))}</div></header>
      <div class="scene-body grid-2"><div class="hero-value"><div class="label">Current Mempool</div><div class="value">{html.escape(compact_number(grpc_metrics.get('mempool_size')))}</div><div class="detail">Latest gRPC mempool size</div></div><div class="chart-panel"><div class="chart-title">Mempool Size By 10s Bucket</div>{mempool_chart}</div></div>
      <footer class="scene-foot"><span>Chart includes latest check appended to history</span><span data-scene-label></span></footer>
    </section>
    <section class="stream-scene" data-scene="market">
      <header class="scene-head"><div><div class="scene-kicker">Market</div><h1>KAS/USDT Market</h1></div><div class="scene-meta">snapshot<br>{html.escape(str(market_checked))}</div></header>
      <div class="scene-body"><div class="metrics-grid compact">{market_metrics_html}</div><div class="chart-panel"><div class="chart-title">Market Snapshot Source</div><div class="hero-value"><div class="label">{html.escape(str(market_metrics.get('source', 'unknown')))}</div><div class="value">{html.escape(format_market_price(spot.get('last_price')))}</div><div class="detail">Persisted snapshot from state/market-snapshots.jsonl</div></div></div></div>
      <footer class="scene-foot"><span>Snapshots: {html.escape(str(market_metrics.get('snapshots', 0)))} total / {html.escape(str(market_metrics.get('successful_snapshots', 0)))} ok</span><span data-scene-label></span></footer>
    </section>
    <section class="stream-scene" data-scene="futures">
      <header class="scene-head"><div><div class="scene-kicker">Futures</div><h1>Futures Positioning</h1></div><div class="scene-meta">KAS/USDT linear<br>{html.escape(str(market_checked))}</div></header>
      <div class="scene-body"><div class="metrics-grid compact">{futures_metrics_html}</div><div class="chart-panel"><div class="chart-title">Operator Context</div><div class="metrics-grid compact">{stream_metric("Status", report.get("status", "unknown"), severity.upper(), severity if severity in {"ok", "warn", "critical"} else "neutral")}{stream_metric("Failed Checks", len(failed), failure_text, severity if severity in {"warn", "critical"} else "neutral")}{stream_metric("Benchmark Window", benchmark_summary.get("window", "unknown"), f"{benchmark_summary.get('snapshots')} snapshots", "neutral")}{stream_metric("Last Alert", str(last_alert_at)[-14:], "state memory", "neutral")}</div></div></div>
      <footer class="scene-foot"><span>Liquidation maps remain available in status.html for manual drill-down</span><span data-scene-label></span></footer>
    </section>
    <div class="progress"><span id="stream-progress"></span></div>
  </main>
  <script>
    const stage = document.getElementById("stream-stage");
    const scenes = Array.from(document.querySelectorAll(".stream-scene"));
    const progress = document.getElementById("stream-progress");
    const params = new URLSearchParams(window.location.search);
    const streamIntervalMs = Math.max(1000, Number(params.get("interval") || stage.dataset.defaultIntervalMs || 5000));
    const pinnedScene = params.get("scene");
    let sceneIndex = Math.max(0, scenes.findIndex((scene) => scene.dataset.scene === pinnedScene));
    let sceneStartedAt = Date.now();

    function scaleStage() {{
      const scale = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
      stage.style.transform = `translate(-50%, -50%) scale(${{scale}})`;
    }}

    function renderScene() {{
      scenes.forEach((scene, index) => {{
        scene.classList.toggle("active", index === sceneIndex);
      }});
      document.querySelectorAll("[data-scene-label]").forEach((label) => {{
        label.textContent = `${{sceneIndex + 1}} / ${{scenes.length}}`;
      }});
      document.querySelectorAll(".scene-dots").forEach((dots) => dots.remove());
      scenes.forEach((scene) => {{
        const dots = document.createElement("span");
        dots.className = "scene-dots";
        scenes.forEach((_, index) => {{
          const dot = document.createElement("span");
          dot.className = "scene-dot" + (index === sceneIndex ? " active" : "");
          dots.appendChild(dot);
        }});
        scene.querySelector(".scene-foot").appendChild(dots);
      }});
      sceneStartedAt = Date.now();
      if (progress) progress.style.width = "0%";
    }}

    function moveScene(delta) {{
      sceneIndex = (sceneIndex + delta + scenes.length) % scenes.length;
      renderScene();
    }}

    window.addEventListener("resize", scaleStage);
    window.addEventListener("keydown", (event) => {{
      if (event.key === "ArrowRight") moveScene(1);
      if (event.key === "ArrowLeft") moveScene(-1);
      if (/^[1-9]$/.test(event.key)) {{
        const target = Number(event.key) - 1;
        if (target < scenes.length) {{
          sceneIndex = target;
          renderScene();
        }}
      }}
    }});

    scaleStage();
    renderScene();
    setInterval(() => {{
      const elapsed = Date.now() - sceneStartedAt;
      if (progress) progress.style.width = `${{Math.min(100, (elapsed / streamIntervalMs) * 100)}}%`;
      if (!pinnedScene && elapsed >= streamIntervalMs) moveScene(1);
    }}, 100);
  </script>
</body>
</html>
"""
    path.write_text(page, encoding="utf-8")


def recent_recovery_records(config: dict, limit: int = 5) -> list[dict[str, Any]]:
    try:
        return load_jsonl(recovery_history_path(config))[-limit:]
    except (OSError, json.JSONDecodeError):
        return []


def sqlite_history_path(config: dict) -> Path:
    return Path(config.get("sqlite_history_path") or DEFAULT_CONFIG["sqlite_history_path"])


def infer_history_network(node_name: str) -> str:
    lowered = node_name.lower()
    if "tn10" in lowered or "testnet" in lowered:
        return "tn10"
    if "mainnet" in lowered:
        return "mainnet"
    return "unknown"


def sqlite_history_window(rows: list[sqlite3.Row], days: int) -> list[sqlite3.Row]:
    dated_rows = [
        (parsed, row)
        for row in rows
        if (parsed := parse_iso_datetime(row["checked_at"])) is not None
    ]
    if not dated_rows:
        return []
    latest_at = max(parsed for parsed, _row in dated_rows)
    cutoff = latest_at - dt.timedelta(days=days)
    return [row for parsed, row in dated_rows if parsed >= cutoff]


def elapsed_minutes(later: Any, earlier: Any) -> float | None:
    later_at = parse_iso_datetime(str(later) if later is not None else None)
    earlier_at = parse_iso_datetime(str(earlier) if earlier is not None else None)
    if later_at is None or earlier_at is None:
        return None
    return (later_at - earlier_at).total_seconds() / 60


def load_multi_node_history_status(db_path: Path, days: int = 7) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": f"missing {db_path}", "nodes": []}
    try:
        with closing(sqlite3.connect(db_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = list(
                connection.execute(
                    """
                    select checked_at, node_name, status, severity, peer_count,
                           virtual_daa_score, block_count,
                           latest_processed_age_seconds
                    from benchmark_snapshots
                    order by checked_at
                    """
                )
            )
    except sqlite3.Error as exc:
        return {"available": False, "reason": str(exc), "nodes": []}

    window = sqlite_history_window(rows, days)
    by_node: dict[str, list[sqlite3.Row]] = {}
    for row in window:
        by_node.setdefault(row["node_name"] or "unknown", []).append(row)
    summaries = []
    for node_name in sorted(by_node):
        node_rows = by_node[node_name]
        latest = node_rows[-1]
        ok_count = sum(1 for row in node_rows if row["status"] == "ok" and row["severity"] == "ok")
        processed_ages = [
            value
            for row in node_rows
            if (value := numeric(row["latest_processed_age_seconds"])) is not None
        ]
        summaries.append(
            {
                "node_name": node_name,
                "network": infer_history_network(node_name),
                "snapshots": len(node_rows),
                "latest_checked_at": latest["checked_at"],
                "latest_status": latest["status"],
                "latest_severity": latest["severity"],
                "ok_ratio": ok_count / len(node_rows) if node_rows else None,
                "latest_peer_count": numeric(latest["peer_count"]),
                "latest_daa_score": numeric(latest["virtual_daa_score"]),
                "latest_block_count": numeric(latest["block_count"]),
                "max_processed_age_seconds": max(processed_ages) if processed_ages else None,
            }
        )

    if not summaries:
        return {"available": True, "verdict": "unknown", "baselines": {}, "nodes": []}

    by_network: dict[str, list[dict[str, Any]]] = {}
    for item in summaries:
        by_network.setdefault(item["network"], []).append(item)
    baselines = {}
    for network, items in by_network.items():
        scored = [item for item in items if item["latest_daa_score"] is not None]
        baselines[network] = max(scored, key=lambda item: item["latest_daa_score"]) if scored else items[0]

    verdict = "ok"
    compared = []
    for item in summaries:
        baseline = baselines[item["network"]]
        flags = []
        daa_lag = None
        block_lag = None
        check_lag = elapsed_minutes(baseline["latest_checked_at"], item["latest_checked_at"])
        peer_lag = None
        processed_age_lag = None
        if baseline["latest_daa_score"] is not None and item["latest_daa_score"] is not None:
            daa_lag = baseline["latest_daa_score"] - item["latest_daa_score"]
            if daa_lag > 120:
                flags.append("daa_lag")
        if baseline["latest_block_count"] is not None and item["latest_block_count"] is not None:
            block_lag = baseline["latest_block_count"] - item["latest_block_count"]
            if block_lag > 120:
                flags.append("block_lag")
        if check_lag is not None and check_lag > 10:
            flags.append("stale_node")
        if baseline["latest_peer_count"] is not None and item["latest_peer_count"] is not None:
            peer_lag = baseline["latest_peer_count"] - item["latest_peer_count"]
            if peer_lag >= 2:
                flags.append("peer_lag")
        if baseline["max_processed_age_seconds"] is not None and item["max_processed_age_seconds"] is not None:
            processed_age_lag = item["max_processed_age_seconds"] - baseline["max_processed_age_seconds"]
            if processed_age_lag >= 60:
                flags.append("processed_age_lag")
        if item["latest_severity"] in {"warn", "critical"}:
            flags.append(f"severity_{item['latest_severity']}")
        if item["latest_status"] != "ok":
            flags.append(f"status_{item['latest_status']}")
        if item["ok_ratio"] is not None and item["ok_ratio"] < 1:
            flags.append("imperfect_ok_ratio")
        if item["latest_peer_count"] is not None and item["latest_peer_count"] <= 0:
            flags.append("no_peers")
        if item["max_processed_age_seconds"] is not None and item["max_processed_age_seconds"] > 120:
            flags.append("processed_stale")
        if item["latest_severity"] == "critical" or "no_peers" in flags:
            verdict = "critical"
        elif flags and verdict == "ok":
            verdict = "warn"
        compared.append(
            {
                **item,
                "baseline_node": baseline["node_name"],
                "check_lag_minutes": check_lag,
                "daa_lag": daa_lag,
                "block_lag": block_lag,
                "peer_lag": peer_lag,
                "processed_age_lag_seconds": processed_age_lag,
                "flags": sorted(set(flags)),
            }
        )

    return {
        "available": True,
        "verdict": verdict,
        "baselines": {network: item["node_name"] for network, item in sorted(baselines.items())},
        "nodes": compared,
    }


def multi_node_history_panel(db_path: Path | None) -> str:
    status = (
        load_multi_node_history_status(db_path)
        if db_path is not None
        else {"available": False, "reason": "SQLite history path not configured", "nodes": []}
    )
    if not status.get("available"):
        return f"""
    <section class="panel">
      <h2>Multi-Node History</h2>
      <div class="subtle">{html.escape(status.get('reason', 'SQLite history unavailable'))}</div>
    </section>
    """
    baseline_text = ", ".join(f"{network}: {node}" for network, node in status.get("baselines", {}).items()) or "none"
    rows = "\n".join(
        html_row(
            [
                item["node_name"],
                item["network"],
                item["latest_severity"],
                format_ratio(item["ok_ratio"]),
                format_optional_number(item["check_lag_minutes"]),
                format_optional_number(item["daa_lag"]),
                format_optional_number(item["block_lag"]),
                format_optional_number(item["peer_lag"]),
                format_optional_number(item["processed_age_lag_seconds"]),
                ",".join(item["flags"]) or "none",
            ]
        )
        for item in status.get("nodes", [])
    )
    if not rows:
        rows = html_row(["No multi-node history", "", "", "", "", "", "", "", "", ""])
    return f"""
    <section class="panel">
      <div class="chart-head">
        <h2>Multi-Node History</h2>
        <div class="chart-value">{html.escape(str(status.get('verdict', 'unknown')).upper())}</div>
      </div>
      <div class="subtle">Baselines: {html.escape(baseline_text)}</div>
      <table>
        <thead>{html_row(["Node", "Network", "Severity", "OK Ratio", "Check Lag Min", "DAA Lag", "Block Lag", "Peer Lag", "Processed Age Lag", "Flags"], "th")}</thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


def wallet_status_panel(report: dict[str, Any], state: dict[str, Any], market_snapshot_path: Path | None = None) -> str:
    wallet = report.get("wallet") or {}
    change = wallet.get("change") or {}
    pending = wallet.get("pending") or {}
    pending_entries = pending.get("entries") or []
    events = list(wallet.get("events") or state.get("wallet_events") or [])
    market_metrics = build_market_metrics(market_snapshot_path or Path(DEFAULT_CONFIG["market_snapshot_path"]))
    latest_market = market_metrics.get("latest_successful") or {}
    latest_price = latest_market.get("spot_last_price")
    mining = mining_reward_summary(events, price_usdt=latest_price)
    address_rows = "\n".join(
        html_row(
            [
                entry.get("label") or "unlabeled",
                entry.get("address") or "",
                format_kas(entry.get("balance_sompi")),
            ]
        )
        for entry in wallet.get("entries") or []
    ) or html_row(["none", "no watch addresses loaded", ""])
    pending_rows = "\n".join(
        html_row(
            [
                item.get("direction", "unknown"),
                item.get("address", ""),
                format_kas(item.get("amount_sompi")) if item.get("amount_sompi") is not None else "unknown",
                format_kas(item.get("fee_sompi")),
                item.get("tx_id") or "unknown",
            ]
        )
        for item in pending_entries[:20]
    ) or html_row(["none", "no pending address txs", "", "", ""])
    event_rows = "\n".join(
        html_row(
            [
                item.get("observed_at", ""),
                item.get("direction", "unknown"),
                item.get("label") or "unlabeled",
                item.get("address") or "",
                format_kas(item.get("delta_sompi")),
            ]
        )
        for item in reversed(events[-20:])
    ) or html_row(["none", "no recorded wallet events", "", "", ""])
    return f"""
    <section id="tab-wallet" class="tab-panel">
    <section class="visual-grid">
      {visual_card("Wallet Total", format_kas(wallet.get("total_sompi")), f"{len(wallet.get('entries') or [])} watch addresses", "neutral")}
      {visual_card("Wallet Delta", format_kas(change.get("total_delta_sompi")), "latest detected change", "neutral")}
      {visual_card("Pending Txs", len(pending_entries), f"mempool ok={pending.get('ok', False)}", "neutral")}
      {visual_card("Mining Today", format_kas(mining.get("today_sompi")), format_usd_amount(mining.get("today_usd")), "neutral")}
      {visual_card("Mining 7D", format_kas(mining.get("seven_day_sompi")), format_usd_amount(mining.get("seven_day_usd")), "neutral")}
      {visual_card("Monthly Run Rate", format_kas((mining.get("projected_monthly_kas") or 0) * 100_000_000), format_usd_amount(mining.get("projected_monthly_usd")), "neutral")}
      {visual_card("Last Reward Age", format_optional_number(mining.get("latest_reward_age_hours")), "hours since mining reward", "neutral")}
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Wallet Status</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Enabled</div><div class="context-value">{html.escape(str(wallet.get('enabled', False)))}</div></div>
          <div class="context-item"><div class="context-label">Read OK</div><div class="context-value">{html.escape(str(wallet.get('ok', False)))}</div></div>
          <div class="context-item"><div class="context-label">Change</div><div class="context-value">{html.escape(str(change.get('changed', False)))}</div></div>
          <div class="context-item"><div class="context-label">Detail</div><div class="context-value">{html.escape(str(wallet.get('detail', 'unknown')))}</div></div>
        </div>
      </section>
      <section class="panel">
        <h2>Mining Rewards</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Candidates</div><div class="context-value">{html.escape(str(mining.get('candidate_events', 0)))}</div></div>
          <div class="context-item"><div class="context-label">Average 7D</div><div class="context-value">{html.escape(format_kas((mining.get('average_daily_7d_kas') or 0) * 100_000_000))}</div></div>
          <div class="context-item"><div class="context-label">Average 30D</div><div class="context-value">{html.escape(format_kas((mining.get('average_daily_30d_kas') or 0) * 100_000_000))}</div></div>
          <div class="context-item"><div class="context-label">Latest Reward</div><div class="context-value">{html.escape(str(mining.get('latest_reward_at') or 'none'))}</div></div>
          <div class="context-item"><div class="context-label">Reward Age</div><div class="context-value">{html.escape(format_optional_number(mining.get('latest_reward_age_hours')))}h</div></div>
          <div class="context-item"><div class="context-label">KAS Price</div><div class="context-value">{html.escape(format_market_price(mining.get('price_usdt')))}</div></div>
          <div class="context-item"><div class="context-label">30D Value</div><div class="context-value">{html.escape(format_usd_amount(mining.get('thirty_day_usd')))}</div></div>
        </div>
      </section>
      <section class="panel">
        <h2>Address Balances</h2>
        <table>
          <thead>{html_row(["Label", "Address", "Balance"], "th")}</thead>
          <tbody>{address_rows}</tbody>
        </table>
      </section>
    </section>
    <section class="panel">
      <h2>Pending Wallet Txs</h2>
      <table>
        <thead>{html_row(["Direction", "Address", "Amount", "Fee", "Tx ID"], "th")}</thead>
        <tbody>{pending_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Wallet Events</h2>
      <table>
        <thead>{html_row(["Observed", "Direction", "Label", "Address", "Delta"], "th")}</thead>
        <tbody>{event_rows}</tbody>
      </table>
    </section>
    </section>
    """


def mining_status_panel(report: dict[str, Any]) -> str:
    mining = report.get("mining") or {}
    process_rows = "\n".join(
        html_row([line])
        for line in mining.get("processes") or []
    ) or html_row(["not running"])
    wallet_address = str(mining.get("wallet_address") or "")
    short_wallet = wallet_address if len(wallet_address) <= 30 else f"{wallet_address[:14]}...{wallet_address[-10:]}"
    last_share_age = mining.get("last_share_age_seconds")
    last_share_text = "unknown" if last_share_age is None else f"{float(last_share_age) / 60:.1f}m"
    return f"""
    <section id="tab-mining" class="tab-panel">
    <section class="visual-grid">
      {visual_card("Miner Mode", mining.get("mode") or "disabled", f"enabled={mining.get('enabled', False)}", "neutral")}
      {visual_card("Miner Status", "running" if mining.get("running") else "stopped", f"ok={mining.get('ok', False)}", "neutral")}
      {visual_card("Hashrate", format_hashrate_local(mining.get("hashrate_hs")), "latest parsed miner log rate", "neutral")}
      {visual_card("Accepted Shares", mining.get("accepted_shares", 0), f"rejected={mining.get('rejected_shares', 0)}", "neutral")}
      {visual_card("Last Share", last_share_text, "age since accepted/rejected share", "neutral")}
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Mining Status</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Enabled</div><div class="context-value">{html.escape(str(mining.get('enabled', False)))}</div></div>
          <div class="context-item"><div class="context-label">Configured</div><div class="context-value">{html.escape(str(mining.get('configured', False)))}</div></div>
          <div class="context-item"><div class="context-label">Mode</div><div class="context-value">{html.escape(str(mining.get('mode') or 'disabled'))}</div></div>
          <div class="context-item"><div class="context-label">Process Match</div><div class="context-value">{html.escape(str(mining.get('process_match') or 'none'))}</div></div>
          <div class="context-item"><div class="context-label">Pool</div><div class="context-value">{html.escape(str(mining.get('pool_url') or 'none'))}</div></div>
          <div class="context-item"><div class="context-label">Worker</div><div class="context-value">{html.escape(str(mining.get('worker_name') or 'none'))}</div></div>
          <div class="context-item"><div class="context-label">Wallet</div><div class="context-value">{html.escape(short_wallet or 'none')}</div></div>
          <div class="context-item"><div class="context-label">Address Source</div><div class="context-value">{html.escape(str(mining.get('wallet_address_source') or 'none'))}</div></div>
          <div class="context-item"><div class="context-label">Detail</div><div class="context-value">{html.escape(str(mining.get('detail', 'unknown')))}</div></div>
        </div>
      </section>
      <section class="panel">
        <h2>macOS GPU Plan</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Phase 1</div><div class="context-value">external miner monitor</div></div>
          <div class="context-item"><div class="context-label">Phase 2</div><div class="context-value">dummy or CPU miner pipeline</div></div>
          <div class="context-item"><div class="context-label">Phase 3</div><div class="context-value">Metal kHeavyHash spike</div></div>
          <div class="context-item"><div class="context-label">Safety</div><div class="context-value">no automatic start/stop in this build</div></div>
        </div>
      </section>
    </section>
    <section class="panel">
      <h2>Miner Processes</h2>
      <table>
        <thead>{html_row(["Process"], "th")}</thead>
        <tbody>{process_rows}</tbody>
      </table>
    </section>
    </section>
    """


def whale_watch_panel(report: dict[str, Any], state: dict[str, Any]) -> str:
    whale = report.get("whale_watch") or {}
    events = list(whale.get("events") or state.get("whale_events") or [])
    summary = whale_watch_summary(events)
    def event_row(item: dict[str, Any]) -> str:
        cells = [
            html.escape(str(item.get("observed_at", ""))),
            html.escape(str(item.get("type", ""))),
            html.escape(format_kas(item.get("amount_sompi"))),
            html_link(item.get("tx_url"), short_hash(item.get("tx_id"))),
            html.escape(str(item.get("source", "unknown"))),
            html.escape(str(item.get("status", ""))),
            html_link(item.get("address_url"), item.get("address") or ""),
        ]
        return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"

    event_rows = "\n".join(
        event_row(item)
        for item in reversed(events[-20:])
    ) or html_row(["none", "no whale events recorded", "", "", "", "", ""])
    return f"""
    <section id="tab-whales" class="tab-panel">
    <section class="visual-grid">
      {visual_card("Whale Threshold", format_kas(whale.get("min_amount_sompi")), "single output minimum", "neutral")}
      {visual_card("24h Whale Count", summary.get("count_24h", 0), "events in last 24h", "neutral")}
      {visual_card("24h Whale Volume", format_kas(summary.get("volume_24h_sompi")), "detected large outputs", "neutral")}
      {visual_card("Latest Whale", format_kas(summary.get("latest_amount_sompi")), short_hash(summary.get("latest_tx_id")) or "none", "neutral")}
      {visual_card("Mempool Scan", whale.get("mempool_entries", 0), f"ok={whale.get('ok', False)}", "neutral")}
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Whale Watch</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Enabled</div><div class="context-value">{html.escape(str(whale.get('enabled', False)))}</div></div>
          <div class="context-item"><div class="context-label">Read OK</div><div class="context-value">{html.escape(str(whale.get('ok', False)))}</div></div>
          <div class="context-item"><div class="context-label">Candidates</div><div class="context-value">{html.escape(str(len(whale.get('candidates') or [])))}</div></div>
          <div class="context-item"><div class="context-label">Confirmed Candidates</div><div class="context-value">{html.escape(str(len(whale.get('confirmed_candidates') or [])))}</div></div>
          <div class="context-item"><div class="context-label">Latest Observed</div><div class="context-value">{html.escape(str(summary.get('latest_observed_at') or 'none'))}</div></div>
          <div class="context-item"><div class="context-label">Detail</div><div class="context-value">{html.escape(str(whale.get('detail', 'unknown')))}</div></div>
          <div class="context-item"><div class="context-label">Confirmed Detail</div><div class="context-value">{html.escape(str(whale.get('confirmed_detail', 'unknown')))}</div></div>
          <div class="context-item"><div class="context-label">Explorer</div><div class="context-value">{html.escape(str(whale.get('explorer_base_url') or 'off'))}</div></div>
        </div>
      </section>
      <section class="panel">
        <h2>Alert Policy</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Alert</div><div class="context-value">{html.escape(str(whale_watch_config({'whale_watch': whale}).get('alert_enabled', True)))}</div></div>
          <div class="context-item"><div class="context-label">Confirmed Scan</div><div class="context-value">{html.escape(str(whale.get('confirmed_enabled', True)))}</div></div>
          <div class="context-item"><div class="context-label">History Limit</div><div class="context-value">{html.escape(str(whale_watch_config({'whale_watch': whale}).get('event_history_entries')))}</div></div>
          <div class="context-item"><div class="context-label">Source</div><div class="context-value">mempool + virtual chain</div></div>
        </div>
      </section>
    </section>
    <section class="panel">
      <h2>Whale Events</h2>
      <table>
        <thead>{html_row(["Observed", "Type", "Amount", "Tx ID", "Source", "Status", "Address"], "th")}</thead>
        <tbody>{event_rows}</tbody>
      </table>
    </section>
    </section>
    """


def indexer_watch_panel(report: dict[str, Any], state: dict[str, Any]) -> str:
    indexer = report.get("indexer") or {}
    watch = report.get("indexer_watch") or {}
    targets = normalize_watch_addresses(watch.get("watch_addresses"))
    address_states = list(watch.get("address_states") or [])
    state_by_address = {str(item.get("address") or ""): item for item in address_states}
    events = list(watch.get("events") or state.get("indexer_watch_events") or [])
    target_rows = "\n".join(
        html_row([
            item.get("label") or "unlabeled",
            item.get("address") or "",
            format_kas((state_by_address.get(str(item.get("address") or "")) or {}).get("balance_sompi")),
            (state_by_address.get(str(item.get("address") or "")) or {}).get("utxo_count", "unknown"),
            (state_by_address.get(str(item.get("address") or "")) or {}).get("tx_count", "unknown"),
            (
                "ok"
                if (state_by_address.get(str(item.get("address") or "")) or {}).get("ok")
                else (state_by_address.get(str(item.get("address") or "")) or {}).get("detail", "pending")
            ),
        ])
        for item in targets
    ) or html_row(["none", "no watched addresses configured", "", "", "", ""])
    event_rows = "\n".join(
        html_row(
            [
                item.get("observed_at", ""),
                item.get("label") or "unlabeled",
                item.get("address") or "",
                short_hash(item.get("tx_id")),
                format_kas(item.get("amount_sompi")) if item.get("amount_sompi") is not None else "unknown",
            ]
        )
        for item in reversed(events[-20:])
    ) or html_row(["none", "no watched address events recorded", "", "", ""])
    command_rows = "\n".join(
        html_row([command])
        for command in [
            "make discord-watch-list",
            'make discord-watch-add ADDRESS="kaspa:..." LABEL="treasury"',
            'make discord-watch-remove ADDRESS="kaspa:..."',
            'make discord-balance ADDRESS="kaspa:..."',
            'make discord-utxos ADDRESS="kaspa:..."',
        ]
    )
    metrics = indexer.get("metrics") or {}
    checkpoint_age = metrics.get("checkpoint_age_seconds")
    checkpoint_age_text = "unknown" if checkpoint_age is None else f"{float(checkpoint_age):.1f}s"
    return f"""
    <section id="tab-indexer" class="tab-panel">
    <section class="visual-grid">
      {visual_card("Indexer State", indexer.get("state") or "disabled", f"ok={indexer.get('ok', False)}", "neutral")}
      {visual_card("Checkpoint Age", checkpoint_age_text, f"fresh={indexer.get('checkpoint_fresh', False)}", "neutral")}
      {visual_card("Watch Addresses", len(targets), f"enabled={watch.get('enabled', False)}", "neutral")}
      {visual_card("Watched Events", len(events), f"new={len(watch.get('new_events') or [])}", "neutral")}
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Indexer Status</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Enabled</div><div class="context-value">{html.escape(str(indexer.get('enabled', False)))}</div></div>
          <div class="context-item"><div class="context-label">State</div><div class="context-value">{html.escape(str(indexer.get('state', 'unknown')))}</div></div>
          <div class="context-item"><div class="context-label">Health OK</div><div class="context-value">{html.escape(str(indexer.get('health_ok', False)))}</div></div>
          <div class="context-item"><div class="context-label">Metrics OK</div><div class="context-value">{html.escape(str(indexer.get('metrics_ok', False)))}</div></div>
          <div class="context-item"><div class="context-label">Lag</div><div class="context-value">{html.escape(str(metrics.get('lag_seconds', 'unknown')))}</div></div>
          <div class="context-item"><div class="context-label">Base URL</div><div class="context-value">{html.escape(str(indexer.get('base_url') or 'none'))}</div></div>
          <div class="context-item"><div class="context-label">Detail</div><div class="context-value">{html.escape(str(indexer.get('detail', 'unknown')))}</div></div>
        </div>
      </section>
      <section class="panel">
        <h2>Indexer Watch Policy</h2>
        <div class="context-grid">
          <div class="context-item"><div class="context-label">Enabled</div><div class="context-value">{html.escape(str(watch.get('enabled', False)))}</div></div>
          <div class="context-item"><div class="context-label">Read OK</div><div class="context-value">{html.escape(str(watch.get('ok', False)))}</div></div>
          <div class="context-item"><div class="context-label">Alert</div><div class="context-value">{html.escape(str(watch.get('alert_enabled', True)))}</div></div>
          <div class="context-item"><div class="context-label">Detail</div><div class="context-value">{html.escape(str(watch.get('detail', 'unknown')))}</div></div>
        </div>
      </section>
    </section>
    <section class="layout">
      <section class="panel">
        <h2>Indexer Watchlist</h2>
        <table>
          <thead>{html_row(["Label", "Address", "Balance", "UTXOs", "Txs", "Status"], "th")}</thead>
          <tbody>{target_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Watch Commands</h2>
        <table>
          <thead>{html_row(["Command"], "th")}</thead>
          <tbody>{command_rows}</tbody>
        </table>
      </section>
    </section>
    <section class="panel">
      <h2>Watched Address Events</h2>
      <table>
        <thead>{html_row(["Observed", "Label", "Address", "Tx ID", "Amount"], "th")}</thead>
        <tbody>{event_rows}</tbody>
      </table>
    </section>
    </section>
    """


def write_status_page(
    path: Path,
    report: dict[str, Any],
    state: dict[str, Any],
    benchmark_path: Path | None = None,
    recovery_records: list[dict[str, Any]] | None = None,
    sqlite_history: Path | None = None,
    market_snapshot_path: Path | None = None,
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
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    indexer = report.get("indexer") or {}
    indexer_metrics = indexer.get("metrics") or {}
    wallet_panel = wallet_status_panel(report, state, market_snapshot_path)
    mining_panel = mining_status_panel(report)
    whale_panel = whale_watch_panel(report, state)
    indexer_panel = indexer_watch_panel(report, state)
    latest_recovery = (recovery_records or [])[-1] if recovery_records else {}
    operator_required = latest_recovery.get("operator_required")
    operator_required_text = "Yes" if operator_required is True else "No" if operator_required is False else "Unknown"
    operator_reason = latest_recovery.get("operator_reason") or latest_recovery.get("reason") or "none"
    failed = failed_check_names(report)
    failure_text = ", ".join(failed) if failed else "None"
    last_alert_at = state.get("last_alert_at") or "None"
    incident_duration = numeric(incident.get("duration_seconds"))
    incident_duration_text = "inactive" if incident_duration is None else f"{incident_duration / 60:.1f}m"
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
    multi_node_panel = multi_node_history_panel(sqlite_history)
    recovery_rows = "\n".join(
        html_row(
            [
                item.get("started_at", ""),
                item.get("action", ""),
                item.get("severity_before", ""),
                item.get("severity_after", ""),
                ",".join(item.get("failed_checks_before") or []),
                "YES" if item.get("operator_required") is True else "no" if item.get("operator_required") is False else "unknown",
                item.get("reason", ""),
            ]
        )
        for item in reversed(recovery_records or [])
    )
    if not recovery_rows:
        recovery_rows = html_row(["No recovery attempts recorded", "", "", "", "", "", ""])
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
    bps_highway = bps_highway_visual(
        processed_rate,
        transactions_per_second=transaction_rate,
        sample_age_seconds=latest_processed_age,
        sample_window_seconds=latest_processed.get("seconds"),
        transaction_count=latest_processed.get("transactions"),
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
            visual_card(
                "Indexer",
                "ok" if indexer.get("ok") else "off" if not indexer.get("enabled") else "alert",
                f"lag {indexer_metrics.get('lag_seconds', 'unknown')}s",
                tone_for_check(report, "indexer_health") if indexer.get("enabled") else "neutral",
            ),
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
        <div><span>Health Score</span><strong>{html.escape(str(report.get('health_score', 'unknown')))}</strong></div>
        <div><span>Incident</span><strong>{html.escape(incident_duration_text)}</strong></div>
        <div><span>Maintenance</span><strong>{html.escape('active' if maintenance.get('active') else 'off')}</strong></div>
        <div><span>Operator Required</span><strong>{html.escape(operator_required_text)} · {html.escape(str(operator_reason))}</strong></div>
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
    .hero-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    h1 {{ font-size: 24px; margin: 0 0 6px; line-height: 1.2; }}
    h2 {{ font-size: 16px; margin: 0 0 12px; }}
    .subtle {{ color: var(--muted); font-size: 13px; max-width: 100%; overflow-wrap: anywhere; word-break: break-word; }}
    .header-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 31px;
      padding: 7px 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--accent);
      font-size: 13px;
      font-weight: 800;
      text-decoration: none;
      white-space: nowrap;
    }}
    .header-link:hover {{ background: var(--accent-soft); }}
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
      grid-template-columns: repeat(4, minmax(0, 1fr));
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
    .market-indicator-panel {{ margin-bottom: 14px; }}
    .market-indicator-card-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }}
    .market-indicator-card {{
      display: grid;
      gap: 7px;
      min-height: 218px;
      padding: 11px;
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #f8fafc;
      min-width: 0;
    }}
    .market-indicator-card.hot {{ background: #fff7ed; border-color: #fed7aa; }}
    .market-indicator-card.cool {{ background: #ecfeff; border-color: #a5f3fc; }}
    .market-indicator-card.neutral {{ background: #f8fafc; border-color: #d9e1e8; }}
    .market-indicator-card .label {{ color: var(--muted); font-size: 12px; font-weight: 900; }}
    .market-indicator-card .value {{ font-size: 24px; line-height: 1; font-weight: 900; overflow-wrap: anywhere; }}
    .market-indicator-card.hot .value {{ color: #b26a00; }}
    .market-indicator-card.cool .value {{ color: #276b74; }}
    .market-indicator-card.neutral .value {{ color: var(--ink); }}
    .market-indicator-card .state {{ color: var(--muted); font-size: 12px; font-weight: 800; overflow-wrap: anywhere; }}
    .market-indicator-rows {{ display: grid; gap: 5px; }}
    .market-indicator-row {{
      display: grid;
      grid-template-columns: 42px 1fr;
      gap: 7px;
      align-items: center;
      min-height: 25px;
      font-size: 12px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }}
    .market-indicator-row span {{ color: var(--muted); }}
    .market-indicator-row strong {{ color: var(--ink); }}
    .market-indicator-row.up strong {{ color: var(--ok); }}
    .market-indicator-row.down strong {{ color: var(--critical); }}
    .market-indicator-row.warn strong {{ color: #b26a00; }}
    .market-indicator-row.cool strong {{ color: #276b74; }}
    .market-bollinger-fill {{ fill: rgba(37, 99, 235, 0.09); }}
    .market-bollinger-line {{ fill: none; stroke: #2563eb; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; opacity: 0.82; }}
    .market-bollinger-mid {{ stroke: #7c3aed; stroke-dasharray: 5 5; opacity: 0.72; }}
    .market-bollinger-label {{ fill: #2563eb; font-size: 11px; font-weight: 800; }}
    .market-chart {{
      width: 100%;
      height: 230px;
      display: block;
      background: #f8fafc;
      border: 1px solid #edf1f6;
      border-radius: 8px;
    }}
    .market-timeframe-grid {{
      display: block;
      margin-bottom: 14px;
    }}
    .timeframe-panel:not(.active),
    .liquidation-panel:not(.active),
    .tab-panel:not(.active) {{ display: none; }}
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
      grid-template-columns: repeat(9, minmax(0, 1fr));
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
    .market-source-panel {{
      margin-bottom: 14px;
    }}
    .market-source-list {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .market-source-row {{
      display: grid;
      grid-template-columns: 72px 1fr;
      gap: 8px;
      align-items: center;
      min-height: 34px;
      padding: 7px 9px;
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #f8fafc;
      font-size: 12px;
      font-weight: 800;
    }}
    .market-source-row .state {{
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }}
    .market-source-row.ok .state {{ color: var(--ok); }}
    .market-source-row.cached .state {{ color: #b26a00; }}
    .market-source-row.fail .state {{ color: var(--critical); }}
    .market-source-row.pending .state {{ color: var(--muted); }}
    .market-source-row .detail {{
      color: var(--muted);
      overflow-wrap: anywhere;
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
    .bps-highway {{
      display: grid;
      gap: 12px;
      min-height: 284px;
      padding: 12px;
      border: 1px solid #d9e1e8;
      border-radius: 8px;
      background: #101923;
      overflow: hidden;
    }}
    .bps-highway-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }}
    .bps-kicker {{ color: #9fe8d4; font-size: 12px; font-weight: 900; text-transform: uppercase; }}
    .bps-rate {{ margin-top: 3px; color: #f8fafc; font-size: 30px; line-height: 1; font-weight: 900; }}
    .bps-state {{ color: #d6e2ef; font-size: 13px; font-weight: 900; text-align: right; }}
    .bps-state span {{ color: #a6b7c8; font-size: 12px; font-weight: 800; }}
    .bps-highway-canvas {{
      width: 100%;
      height: 310px;
      display: block;
      border-radius: 8px;
      background: linear-gradient(180deg, #111827, #020617);
    }}
    .bps-highway.three-ready .bps-road {{ display: none; }}
    .bps-highway.three-fallback .bps-highway-canvas {{ display: none; }}
    .bps-road {{ display: grid; grid-template-rows: repeat(20, 1fr); gap: 3px; min-height: 172px; }}
    .bps-lane {{
      position: relative;
      overflow: hidden;
      min-height: 6px;
      border-top: 1px dashed rgba(217, 225, 232, 0.22);
      border-radius: 3px;
      background: rgba(255, 255, 255, 0.05);
    }}
    .bps-lane.active {{ background: rgba(20, 125, 100, 0.22); }}
    .bps-car {{
      position: absolute;
      top: 50%;
      left: -42px;
      width: var(--car-width);
      height: 7px;
      border-radius: 999px;
      background: #35c58f;
      box-shadow: 0 0 10px rgba(53, 197, 143, 0.42);
      transform: translateY(-50%);
      animation: bps-drive var(--speed) linear infinite;
      animation-delay: var(--delay);
    }}
    .bps-highway.warn .bps-car {{ background: #f0a000; box-shadow: 0 0 10px rgba(240, 160, 0, 0.45); }}
    .bps-highway.critical .bps-car {{ background: #ef6b5f; box-shadow: 0 0 10px rgba(239, 107, 95, 0.50); }}
    .bps-caption {{ color: #a6b7c8; font-size: 12px; font-weight: 800; }}
    @keyframes bps-drive {{ from {{ transform: translate(-50px, -50%); }} to {{ transform: translate(1220px, -50%); }} }}
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
    .status-tabs,
    .subtab-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 0 0 14px;
    }}
    .tab-button,
    .subtab-button {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      color: var(--ink);
      cursor: pointer;
      font-size: 13px;
      font-weight: 900;
      min-height: 36px;
      padding: 7px 11px;
    }}
    .tab-button.active,
    .subtab-button.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #ffffff;
    }}
    .tab-panel {{
      margin-bottom: 14px;
    }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    code {{ background: #eef2f6; padding: 2px 4px; border-radius: 4px; overflow-wrap: anywhere; word-break: break-all; white-space: normal; }}
    .ok-text {{ color: var(--ok); font-weight: 700; }}
    .fail-text {{ color: var(--critical); font-weight: 700; }}
    main > .panel + .panel {{ margin-top: 14px; }}
    @media (max-width: 760px) {{
      main {{ padding: 14px; }}
      .hero-top, .hero-strip, .layout, .chart-grid, .market-watch, .market-timeframe-grid, .market-indicator-card-grid, .liquidation-grid, .context-grid {{ display: block; }}
      .hero-actions {{ justify-content: flex-start; margin-top: 12px; }}
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
      .market-indicator-card {{ margin-bottom: 10px; }}
      .futures-panel .market-meta {{ grid-template-columns: 1fr; }}
      .market-source-list {{ grid-template-columns: 1fr; }}
      .panel {{ overflow-x: auto; }}
      .bps-highway-head {{ display: block; }}
      .bps-state {{ text-align: left; margin-top: 6px; }}
      .bar-block, .context-item {{ margin-bottom: 10px; }}
      .status-tabs, .subtab-row {{ display: grid; grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="hero-top">
        <div>
          <h1>Kaspa Node Watchtower</h1>
          <div class="subtle">{html.escape(report['node_name'])} · checked at <code data-watchtower-checked-at>{html.escape(report['checked_at'])}</code> · BPS live refresh 5s · page refresh 60s</div>
        </div>
        <div class="hero-actions">
          <a class="header-link" href="stream.html">Stream</a>
          <div class="badge {html.escape(report['severity'])}">{html.escape(report['severity'])}</div>
        </div>
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
    <nav class="status-tabs" aria-label="Status dashboard sections">
      <button class="tab-button active" type="button" data-tab-target="tab-market">Market</button>
      <button class="tab-button" type="button" data-tab-target="tab-futures">Futures</button>
      <button class="tab-button" type="button" data-tab-target="tab-network">Network</button>
      <button class="tab-button" type="button" data-tab-target="tab-wallet">Wallet</button>
      <button class="tab-button" type="button" data-tab-target="tab-mining">Mining</button>
      <button class="tab-button" type="button" data-tab-target="tab-indexer">Indexer</button>
      <button class="tab-button" type="button" data-tab-target="tab-whales">Whales</button>
      <button class="tab-button" type="button" data-tab-target="tab-ops">Ops</button>
      <button class="tab-button" type="button" data-tab-target="tab-history">History</button>
      <a class="tab-button" href="/sns/">SNS</a>
      <a class="tab-button" href="/game/">Game</a>
      <a class="tab-button" href="/games/">Games</a>
    </nav>
    <section id="tab-market" class="tab-panel active">
    <div class="subtab-row" aria-label="KAS/USDT timeframe selector">
      <button class="subtab-button active" type="button" data-timeframe-target="15m">15m</button>
      <button class="subtab-button" type="button" data-timeframe-target="4h">4h</button>
      <button class="subtab-button" type="button" data-timeframe-target="1d">1D</button>
      <button class="subtab-button" type="button" data-timeframe-target="1w">1W</button>
      <button class="subtab-button" type="button" data-timeframe-target="1m">1M</button>
      <button class="subtab-button" type="button" data-timeframe-target="all">All</button>
    </div>
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
      <section class="panel timeframe-panel active" data-timeframe-panel="15m">
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
    <section class="panel market-indicator-panel">
      <div class="market-chart-head">
        <h2>Market Indicators</h2>
        <div class="market-status">RSI, MACD, Bollinger, volume, and BTC-relative by timeframe</div>
      </div>
      <div class="market-indicator-card-grid">
        <div id="market-rsi-card-15m" class="market-indicator-card" data-indicator-card="15m">
          <div class="label">15m</div>
          <div class="value" data-indicator-value="rsi">RSI --</div>
          <div class="state" data-indicator-state="rsi">Waiting for candles</div>
          <div class="market-indicator-rows">
            <div class="market-indicator-row" data-indicator-row="macd"><span>MACD</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="bb"><span>BB</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="volume"><span>Vol</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="relative"><span>BTC</span><strong>pending</strong></div>
          </div>
        </div>
        <div id="market-rsi-card-4h" class="market-indicator-card" data-indicator-card="4h">
          <div class="label">4h</div>
          <div class="value" data-indicator-value="rsi">RSI --</div>
          <div class="state" data-indicator-state="rsi">Waiting for candles</div>
          <div class="market-indicator-rows">
            <div class="market-indicator-row" data-indicator-row="macd"><span>MACD</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="bb"><span>BB</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="volume"><span>Vol</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="relative"><span>BTC</span><strong>pending</strong></div>
          </div>
        </div>
        <div id="market-rsi-card-1d" class="market-indicator-card" data-indicator-card="1D">
          <div class="label">1D</div>
          <div class="value" data-indicator-value="rsi">RSI --</div>
          <div class="state" data-indicator-state="rsi">Waiting for candles</div>
          <div class="market-indicator-rows">
            <div class="market-indicator-row" data-indicator-row="macd"><span>MACD</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="bb"><span>BB</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="volume"><span>Vol</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="relative"><span>BTC</span><strong>pending</strong></div>
          </div>
        </div>
        <div id="market-rsi-card-1w" class="market-indicator-card" data-indicator-card="1W">
          <div class="label">1W</div>
          <div class="value" data-indicator-value="rsi">RSI --</div>
          <div class="state" data-indicator-state="rsi">Waiting for candles</div>
          <div class="market-indicator-rows">
            <div class="market-indicator-row" data-indicator-row="macd"><span>MACD</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="bb"><span>BB</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="volume"><span>Vol</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="relative"><span>BTC</span><strong>pending</strong></div>
          </div>
        </div>
        <div id="market-rsi-card-1m" class="market-indicator-card" data-indicator-card="1M">
          <div class="label">1M</div>
          <div class="value" data-indicator-value="rsi">RSI --</div>
          <div class="state" data-indicator-state="rsi">Waiting for candles</div>
          <div class="market-indicator-rows">
            <div class="market-indicator-row" data-indicator-row="macd"><span>MACD</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="bb"><span>BB</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="volume"><span>Vol</span><strong>pending</strong></div>
            <div class="market-indicator-row" data-indicator-row="relative"><span>BTC</span><strong>pending</strong></div>
          </div>
        </div>
      </div>
    </section>
    <section class="market-timeframe-grid">
      <section class="panel timeframe-panel" data-timeframe-panel="4h">
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
      <section class="panel timeframe-panel" data-timeframe-panel="1d">
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
      <section class="panel timeframe-panel" data-timeframe-panel="1w">
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
      <section class="panel timeframe-panel" data-timeframe-panel="1m">
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
      <section class="panel timeframe-panel" data-timeframe-panel="all">
        <div class="market-chart-head">
          <div class="market-title-row">
            <h2>KAS/USDT All Bollinger</h2>
            <span id="market-trend-all" class="market-trend-badge">Trend pending</span>
            <span id="market-rsi-all" class="market-rsi-badge">RSI pending</span>
          </div>
          <div id="market-status-all" class="market-status">Loading full-period bands</div>
        </div>
        <div class="market-legend">
          <span style="color: #2563eb"><i></i>BB upper/lower</span>
          <span style="color: #7c3aed"><i></i>BB basis</span>
          <span style="color: #d97706"><i></i>EMA</span>
        </div>
        <svg id="market-chart-all" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="Full-period KAS/USDT candlestick chart with Bollinger Bands"></svg>
      </section>
    </section>
    <section class="panel market-cross-panel">
      <div class="market-chart-head">
        <h2>KAS/USDT vs BTC/USDT 15m</h2>
        <div id="market-cross-status-15m" class="market-status">Loading 15m cross</div>
      </div>
      <div class="market-legend">
        <span style="color: #b42318"><i></i>KAS/USDT</span>
        <span style="color: #2563eb"><i></i>BTC/USDT</span>
      </div>
      <svg id="market-cross-chart-15m" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT and BTC/USDT 15 minute normalized comparison chart"></svg>
    </section>
    <section class="panel market-cross-panel">
      <div class="market-chart-head">
        <h2>KAS/USDT vs BTC/USDT 4h</h2>
        <div id="market-cross-status-4h" class="market-status">Loading 4h cross</div>
      </div>
      <div class="market-legend">
        <span style="color: #b42318"><i></i>KAS/USDT</span>
        <span style="color: #2563eb"><i></i>BTC/USDT</span>
      </div>
      <svg id="market-cross-chart-4h" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT and BTC/USDT 4 hour normalized comparison chart"></svg>
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
    <section class="panel market-cross-panel">
      <div class="market-chart-head">
        <h2>KAS/USDT vs BTC/USDT 1W</h2>
        <div id="market-cross-status-1w" class="market-status">Loading weekly cross</div>
      </div>
      <div class="market-legend">
        <span style="color: #b42318"><i></i>KAS/USDT</span>
        <span style="color: #2563eb"><i></i>BTC/USDT</span>
      </div>
      <svg id="market-cross-chart-1w" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT and BTC/USDT weekly normalized comparison chart"></svg>
    </section>
    <section class="panel market-cross-panel">
      <div class="market-chart-head">
        <h2>KAS/USDT vs BTC/USDT 1M</h2>
        <div id="market-cross-status-1m" class="market-status">Loading monthly cross</div>
      </div>
      <div class="market-legend">
        <span style="color: #b42318"><i></i>KAS/USDT</span>
        <span style="color: #2563eb"><i></i>BTC/USDT</span>
      </div>
      <svg id="market-cross-chart-1m" class="market-chart" viewBox="0 0 720 230" role="img" aria-label="KAS/USDT and BTC/USDT monthly normalized comparison chart"></svg>
    </section>
    <section class="panel market-volume-panel">
      <div class="market-chart-head">
        <h2>KAS Exchange Volume 1D</h2>
        <div id="market-volume-status" class="market-status">Loading exchange volumes</div>
      </div>
      <div id="market-volume-legend" class="market-legend"></div>
      <svg id="market-volume-chart" class="market-chart" viewBox="0 0 720 244" role="img" aria-label="Daily KAS trading volume by exchange and total"></svg>
    </section>
    </section>
    <section id="tab-futures" class="tab-panel">
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
        <div class="context-item"><div class="context-label">Index</div><div id="futures-index" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Basis</div><div id="futures-basis" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Funding</div><div id="futures-funding" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Funding APR</div><div id="futures-funding-apr" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Next funding</div><div id="futures-next-funding" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Open interest</div><div id="futures-open-interest" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">OI value</div><div id="futures-open-interest-value" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">24h vol</div><div id="futures-volume" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Risk score</div><div id="futures-risk" class="context-value">unknown</div></div>
        <div class="context-item"><div class="context-label">Risk reasons</div><div id="futures-risk-reasons" class="context-value">unknown</div></div>
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
    <section class="panel market-source-panel">
      <div class="market-chart-head">
        <h2>Market Data Sources</h2>
        <div class="market-status">Live, cached, or unavailable public API groups</div>
      </div>
      <div id="market-source-list" class="market-source-list">
        <div class="market-source-row pending"><span class="state">pending</span><span class="detail">Waiting for first market refresh</span></div>
      </div>
    </section>
    <div class="subtab-row" aria-label="Liquidation map range selector">
      <button class="subtab-button active" type="button" data-liquidation-target="12h">12H</button>
      <button class="subtab-button" type="button" data-liquidation-target="24h">24H</button>
      <button class="subtab-button" type="button" data-liquidation-target="1w">1W</button>
      <button class="subtab-button" type="button" data-liquidation-target="1m">1M</button>
    </div>
    <section class="liquidation-grid">
      <section class="panel liquidation-panel active" data-liquidation-panel="12h">
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
      <section class="panel liquidation-panel" data-liquidation-panel="24h">
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
      <section class="panel liquidation-panel" data-liquidation-panel="1w">
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
      <section class="panel liquidation-panel" data-liquidation-panel="1m">
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
    </section>
    <section id="tab-network" class="tab-panel">
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
    <section class="panel" data-bps-live-panel>
      <div class="chart-head">
        <h2>BPS Highway</h2>
        <div class="chart-value" data-bps-panel-rate>{html.escape(processed_rate_text)}</div>
      </div>
      <div class="subtle">20-lane visual flow mapped from current processed blocks per second</div>
      {bps_highway}
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
    </section>
    {wallet_panel}
    {mining_panel}
    {indexer_panel}
    {whale_panel}
    <section id="tab-ops" class="tab-panel">
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
    </section>
    <section id="tab-history" class="tab-panel">
    {multi_node_panel}
    <section class="panel">
      <h2>Benchmark Trend</h2>
      <table>
        <tbody>{benchmark_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Recent Recovery</h2>
      <table>
        <thead>{html_row(["Started At", "Action", "Before", "After", "Failed Before", "Operator Required", "Reason"], "th")}</thead>
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
    </section>
  </main>
  <script>
    function activateDashboardGroup(buttonSelector, panelSelector, buttonAttr, panelAttr, target) {{
      document.querySelectorAll(buttonSelector).forEach((button) => {{
        button.classList.toggle("active", button.getAttribute(buttonAttr) === target);
      }});
      document.querySelectorAll(panelSelector).forEach((panel) => {{
        panel.classList.toggle("active", panel.getAttribute(panelAttr) === target);
      }});
    }}

    const statusActiveTabKey = "kaspa-watchtower-active-tab";
    const statusActiveTimeframeKey = "kaspa-watchtower-active-timeframe";
    const statusActiveLiquidationKey = "kaspa-watchtower-active-liquidation";

    function storeDashboardSelection(key, value) {{
      if (!value || !window.localStorage) {{
        return;
      }}
      try {{
        window.localStorage.setItem(key, value);
      }} catch (_error) {{
        // Ignore storage failures in private browsing or locked-down WebViews.
      }}
    }}

    function loadDashboardSelection(key) {{
      if (!window.localStorage) {{
        return "";
      }}
      try {{
        return window.localStorage.getItem(key) || "";
      }} catch (_error) {{
        return "";
      }}
    }}

    function validDashboardTarget(buttonSelector, attr, target) {{
      if (!target) {{
        return "";
      }}
      const button = document.querySelector(`${{buttonSelector}}[${{attr}}="${{CSS.escape(target)}}"]`);
      return button ? target : "";
    }}

    function restoreDashboardSelection() {{
      const hashTarget = window.location.hash ? window.location.hash.slice(1) : "";
      const tabTarget = validDashboardTarget(
        ".tab-button",
        "data-tab-target",
        hashTarget || loadDashboardSelection(statusActiveTabKey),
      );
      const timeframeTarget = validDashboardTarget(
        "[data-timeframe-target]",
        "data-timeframe-target",
        loadDashboardSelection(statusActiveTimeframeKey),
      );
      const liquidationTarget = validDashboardTarget(
        "[data-liquidation-target]",
        "data-liquidation-target",
        loadDashboardSelection(statusActiveLiquidationKey),
      );
      if (tabTarget) {{
        activateDashboardGroup(".tab-button", ".tab-panel", "data-tab-target", "id", tabTarget);
      }}
      if (timeframeTarget) {{
        activateDashboardGroup("[data-timeframe-target]", "[data-timeframe-panel]", "data-timeframe-target", "data-timeframe-panel", timeframeTarget);
      }}
      if (liquidationTarget) {{
        activateDashboardGroup("[data-liquidation-target]", "[data-liquidation-panel]", "data-liquidation-target", "data-liquidation-panel", liquidationTarget);
      }}
    }}

    document.querySelectorAll("[data-tab-target]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const target = button.getAttribute("data-tab-target");
        activateDashboardGroup(".tab-button", ".tab-panel", "data-tab-target", "id", target);
        storeDashboardSelection(statusActiveTabKey, target);
        if (window.history && window.history.replaceState) {{
          window.history.replaceState(null, "", `#${{target}}`);
        }}
      }});
    }});

    document.querySelectorAll("[data-timeframe-target]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const target = button.getAttribute("data-timeframe-target");
        activateDashboardGroup("[data-timeframe-target]", "[data-timeframe-panel]", "data-timeframe-target", "data-timeframe-panel", target);
        storeDashboardSelection(statusActiveTimeframeKey, target);
      }});
    }});

    document.querySelectorAll("[data-liquidation-target]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const target = button.getAttribute("data-liquidation-target");
        activateDashboardGroup("[data-liquidation-target]", "[data-liquidation-panel]", "data-liquidation-target", "data-liquidation-panel", target);
        storeDashboardSelection(statusActiveLiquidationKey, target);
      }});
    }});

    restoreDashboardSelection();

    function bpsNumber(value) {{
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }}

    function bpsPayloadFromState(state) {{
      const report = state && state.last_report ? state.last_report : {{}};
      const progress = report.progress || {{}};
      const latest = progress.latest_processed || {{}};
      const rate = bpsNumber(latest.blocks_per_second) || 0;
      const txRate = bpsNumber(latest.transactions_per_second);
      const age = bpsNumber(progress.latest_processed_age_seconds);
      const windowSeconds = bpsNumber(latest.seconds);
      const transactions = latest.transactions;
      const laneCount = 20;
      const capacityBps = 20;
      const usage = Math.max(0, Math.min(1, rate / capacityBps));
      let tone = "ok";
      let flowState = "clear flow";
      if (usage >= 0.9) {{
        tone = "critical";
        flowState = "near capacity";
      }} else if (usage >= 0.7) {{
        tone = "warn";
        flowState = "heavy flow";
      }}
      return {{
        rate,
        txRate,
        age,
        windowSeconds,
        transactions,
        laneCount,
        usage,
        tone,
        flowState,
      }};
    }}

    function formatBpsRate(value) {{
      return `${{value.toFixed(1)}} BPS`;
    }}

    function updateBpsHighwayFromState(state) {{
      const bps = bpsPayloadFromState(state);
      const highway = document.querySelector("[data-bps-highway]");
      if (!highway) {{
        return;
      }}
      const rateText = formatBpsRate(bps.rate);
      const panelRate = document.querySelector("[data-bps-panel-rate]");
      if (panelRate) {{
        panelRate.textContent = bps.rate ? `${{bps.rate.toFixed(1)}}/s` : "unknown";
      }}
      const rateElement = highway.querySelector("[data-bps-rate]");
      if (rateElement) {{
        rateElement.textContent = rateText;
      }}
      const stateElement = highway.querySelector("[data-bps-state]");
      if (stateElement) {{
        stateElement.textContent = `${{bps.flowState}} · ${{Math.round(bps.usage * 100)}}%`;
      }}
      const txElement = highway.querySelector("[data-bps-tx-rate]");
      if (txElement) {{
        txElement.textContent = bps.txRate === null ? "unknown tx/s" : `${{bps.txRate.toFixed(1)}} tx/s`;
      }}
      const caption = highway.querySelector("[data-bps-caption]");
      if (caption) {{
        const activeLanes = bps.rate > 0 ? bps.laneCount : 0;
        const ageText = bps.age === null ? "unknown age" : `${{bps.age}}s old`;
        const txCountText = bps.transactions === undefined || bps.transactions === null ? "unknown tx" : `${{bps.transactions}} tx`;
        const windowText = bps.windowSeconds === null ? "unknown window" : `${{bps.windowSeconds}}s window`;
        caption.textContent = `${{activeLanes}}/${{bps.laneCount}} lanes open · rusty-kaspa processed-stats log · ${{ageText}} · ${{txCountText}} / ${{windowText}}`;
      }}
      highway.classList.remove("ok", "warn", "critical");
      highway.classList.add(bps.tone);
      const canvas = highway.querySelector("canvas.bps-highway-canvas");
      if (canvas) {{
        canvas.dataset.highwayPayload = JSON.stringify({{
          rate: bps.rate,
          usage: bps.usage,
          laneCount: bps.laneCount,
          tone: bps.tone,
        }});
      }}
    }}

    async function pollWatchtowerState() {{
      try {{
        let response = await fetch("bps-highway.json?ts=" + Date.now(), {{ cache: "no-store" }});
        if (!response.ok) {{
          response = await fetch("watchtower-state.json?ts=" + Date.now(), {{ cache: "no-store" }});
        }}
        if (!response.ok) {{
          return;
        }}
        const state = await response.json();
        const checkedAt = state && state.checked_at ? String(state.checked_at) : "";
        const checkedElement = document.querySelector("[data-watchtower-checked-at]");
        if (checkedElement && checkedAt) {{
          checkedElement.textContent = checkedAt;
        }}
        updateBpsHighwayFromState(state);
      }} catch (error) {{
        // Local file views can block fetch(); the 60s meta refresh remains the fallback.
      }}
    }}
    pollWatchtowerState();
    window.setInterval(pollWatchtowerState, 5000);

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
          rsiCardId: "market-rsi-card-15m",
          refreshMs: 60 * 1000,
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
          rsiCardId: "market-rsi-card-4h",
          refreshMs: 5 * 60 * 1000,
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
          rsiCardId: "market-rsi-card-1d",
          refreshMs: 10 * 60 * 1000,
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
          rsiCardId: "market-rsi-card-1w",
          refreshMs: 30 * 60 * 1000,
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
          rsiCardId: "market-rsi-card-1m",
          refreshMs: 60 * 60 * 1000,
          url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=M",
        }},
        {{
          label: "All",
          emaPeriod: 30,
          axisMode: "month",
          limit: 1000,
          chartId: "market-chart-all",
          statusId: "market-status-all",
          trendId: "market-trend-all",
          rsiId: "market-rsi-all",
          refreshMs: 60 * 60 * 1000,
          bollinger: {{ period: 20, deviations: 2 }},
          url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=D",
        }},
      ],
      cross: [
        {{
          label: "15m",
          indicatorCardId: "market-rsi-card-15m",
          chartId: "market-cross-chart-15m",
          statusId: "market-cross-status-15m",
          refreshMs: 60 * 1000,
          series: [
            {{
              label: "KAS/USDT",
              color: "#b42318",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=15&limit=96",
            }},
            {{
              label: "BTC/USDT",
              color: "#2563eb",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=15&limit=96",
            }},
          ],
        }},
        {{
          label: "4h",
          indicatorCardId: "market-rsi-card-4h",
          chartId: "market-cross-chart-4h",
          statusId: "market-cross-status-4h",
          refreshMs: 5 * 60 * 1000,
          series: [
            {{
              label: "KAS/USDT",
              color: "#b42318",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=240&limit=48",
            }},
            {{
              label: "BTC/USDT",
              color: "#2563eb",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=240&limit=48",
            }},
          ],
        }},
        {{
          label: "1D",
          indicatorCardId: "market-rsi-card-1d",
          chartId: "market-cross-chart",
          statusId: "market-cross-status",
          axisMode: "day",
          refreshMs: 10 * 60 * 1000,
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
        {{
          label: "1W",
          indicatorCardId: "market-rsi-card-1w",
          chartId: "market-cross-chart-1w",
          statusId: "market-cross-status-1w",
          axisMode: "month",
          refreshMs: 30 * 60 * 1000,
          series: [
            {{
              label: "KAS/USDT",
              color: "#b42318",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=W&limit=60",
            }},
            {{
              label: "BTC/USDT",
              color: "#2563eb",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=W&limit=60",
            }},
          ],
        }},
        {{
          label: "1M",
          indicatorCardId: "market-rsi-card-1m",
          chartId: "market-cross-chart-1m",
          statusId: "market-cross-status-1m",
          axisMode: "year",
          independentRange: true,
          refreshMs: 60 * 60 * 1000,
          series: [
            {{
              label: "KAS/USDT",
              color: "#b42318",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=KASUSDT&interval=M&limit=1000",
            }},
            {{
              label: "BTC/USDT",
              color: "#2563eb",
              url: "https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=M&limit=1000",
            }},
          ],
        }},
      ],
      volume: {{
        chartId: "market-volume-chart",
        statusId: "market-volume-status",
        legendId: "market-volume-legend",
        limit: 32,
        refreshMs: 10 * 60 * 1000,
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
          refreshMs: 60 * 1000,
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=15&limit=48",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=15min&limit=48",
        }},
        {{
          label: "24H",
          chartId: "liquidation-chart-24h",
          statusId: "liquidation-status-24h",
          refreshMs: 60 * 1000,
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=30&limit=48",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=30min&limit=48",
        }},
        {{
          label: "1W",
          chartId: "liquidation-chart-1w",
          statusId: "liquidation-status-1w",
          refreshMs: 5 * 60 * 1000,
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=240&limit=42",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=4h&limit=42",
        }},
        {{
          label: "1M",
          chartId: "liquidation-chart-1m",
          statusId: "liquidation-status-1m",
          refreshMs: 10 * 60 * 1000,
          klineUrl: "https://api.bybit.com/v5/market/kline?category=linear&symbol=KASUSDT&interval=D&limit=32",
          openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=1d&limit=32",
        }},
      ],
      futuresTrend: {{
        chartId: "futures-trend-chart",
        statusId: "futures-trend-status",
        refreshMs: 5 * 60 * 1000,
        openInterestUrl: "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=KASUSDT&intervalTime=4h&limit=42",
        fundingUrl: "https://api.bybit.com/v5/market/funding/history?category=linear&symbol=KASUSDT&limit=42",
      }},
    }};
    const marketSignals = new Map();
    const marketRefreshTimes = new Map();
    const marketSourceStates = new Map();
    const marketSourceOrder = [
      ["spot-ticker", "Spot ticker"],
      ["spot-15m", "Spot 15m"],
      ["spot-4h", "Spot 4h"],
      ["spot-1D", "Spot 1D"],
      ["spot-1W", "Spot 1W"],
      ["spot-1M", "Spot 1M"],
      ["spot-All", "Spot All"],
      ["cross-15m", "KAS/BTC cross 15m"],
      ["cross-4h", "KAS/BTC cross 4h"],
      ["cross-1D", "KAS/BTC cross 1D"],
      ["cross-1W", "KAS/BTC cross 1W"],
      ["cross-1M", "KAS/BTC cross 1M"],
      ["volume", "Exchange volume"],
      ["futures-positioning", "Futures positioning"],
      ["futures-trend", "Futures trend"],
      ["liquidation-12H", "Liquidation 12H"],
      ["liquidation-24H", "Liquidation 24H"],
      ["liquidation-1W", "Liquidation 1W"],
      ["liquidation-1M", "Liquidation 1M"],
    ];

    function marketShouldRefresh(key, refreshMs) {{
      const interval = Number(refreshMs || 0);
      if (!Number.isFinite(interval) || interval <= 0) {{
        return true;
      }}
      const now = Date.now();
      const previous = marketRefreshTimes.get(key) || 0;
      if (now - previous < interval) {{
        return false;
      }}
      marketRefreshTimes.set(key, now);
      return true;
    }}

    function marketText(id, value) {{
      const element = document.getElementById(id);
      if (element) {{
        element.textContent = value;
      }}
    }}

    function marketSourceDetail(payload) {{
      const time = Number((payload || {{}}).cachedAt || (payload || {{}}).time || Date.now());
      return ((payload || {{}}).fromCache ? "cached " : "live ") + new Date(time).toLocaleTimeString();
    }}

    function marketErrorDetail(error) {{
      const message = error && (error.message || String(error));
      if (!message) {{
        return "unavailable";
      }}
      return String(message).slice(0, 120);
    }}

    function marketRenderSourceStates() {{
      const list = document.getElementById("market-source-list");
      if (!list) {{
        return;
      }}
      list.replaceChildren();
      const ordered = marketSourceOrder.map((entry) => {{
        const key = entry[0];
        const label = entry[1];
        return marketSourceStates.get(key) || {{ label, state: "pending", detail: "waiting for refresh" }};
      }});
      marketSourceStates.forEach((value, key) => {{
        if (!marketSourceOrder.some((entry) => entry[0] === key)) {{
          ordered.push(value);
        }}
      }});
      ordered.forEach((item) => {{
        const row = document.createElement("div");
        row.className = "market-source-row " + item.state;

        const stateElement = document.createElement("span");
        stateElement.className = "state";
        stateElement.textContent = item.state;
        row.appendChild(stateElement);

        const detailElement = document.createElement("span");
        detailElement.className = "detail";
        detailElement.textContent = item.label + ": " + item.detail;
        row.appendChild(detailElement);

        list.appendChild(row);
      }});
    }}

    function marketSourceStatus(key, label, state, detail) {{
      marketSourceStates.set(key, {{ label, state, detail }});
      marketRenderSourceStates();
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

    function marketPositioningRisk(fundingZ, oiVolumeRatio, basisPct, dispersionPct) {{
      const reasons = [];
      let score = 0;
      const funding = marketNumber(fundingZ);
      const oiVolume = marketNumber(oiVolumeRatio);
      const basis = marketNumber(basisPct);
      const dispersion = marketNumber(dispersionPct);
      if (funding !== null && Math.abs(funding) >= 3) {{
        score += 2;
        reasons.push("funding_z_extreme");
      }} else if (funding !== null && Math.abs(funding) >= 2) {{
        score += 1;
        reasons.push("funding_z_elevated");
      }}
      if (oiVolume !== null && oiVolume >= 5) {{
        score += 2;
        reasons.push("oi_volume_extreme");
      }} else if (oiVolume !== null && oiVolume >= 3) {{
        score += 1;
        reasons.push("oi_volume_elevated");
      }}
      if (basis !== null && Math.abs(basis) >= 1.5) {{
        score += 1;
        reasons.push("basis_wide");
      }}
      if (dispersion !== null && dispersion >= 2) {{
        score += 1;
        reasons.push("spot_dispersion_wide");
      }}
      let level = "ok";
      if (score >= 4) {{
        level = "critical";
      }} else if (score >= 2) {{
        level = "warning";
      }}
      let direction = "neutral";
      if ((funding !== null && funding > 0) || (basis !== null && basis > 0)) {{
        direction = "long_crowded";
      }}
      if ((funding !== null && funding < 0) || (basis !== null && basis < 0)) {{
        direction = direction === "neutral" ? "short_crowded" : "mixed";
      }}
      return {{ score, level, direction, reasons }};
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
          volume: marketNumber(row[5]) || 0,
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

    function marketBollingerPoints(candles, period, deviations) {{
      const parsedPeriod = Number(period);
      const parsedDeviations = Number(deviations || 2);
      if (!Number.isFinite(parsedPeriod) || parsedPeriod <= 1 || candles.length < parsedPeriod) {{
        return [];
      }}
      return candles.map((candle, index) => {{
        if (index + 1 < parsedPeriod) {{
          return {{ time: candle.time, basis: null, upper: null, lower: null }};
        }}
        const windowCandles = candles.slice(index + 1 - parsedPeriod, index + 1);
        const closes = windowCandles.map((item) => item.close);
        const basis = closes.reduce((total, value) => total + value, 0) / parsedPeriod;
        const variance = closes.reduce((total, value) => total + Math.pow(value - basis, 2), 0) / parsedPeriod;
        const standardDeviation = Math.sqrt(variance);
        return {{
          time: candle.time,
          basis,
          upper: basis + standardDeviation * parsedDeviations,
          lower: basis - standardDeviation * parsedDeviations,
        }};
      }});
    }}

    function marketMacdState(candles) {{
      if (candles.length < 35) {{
        return {{ tone: "", text: "pending", detail: "Not enough MACD data" }};
      }}
      const fast = marketEmaPoints(candles, 12);
      const slow = marketEmaPoints(candles, 26);
      const macdSeries = candles.map((candle, index) => ({{
        time: candle.time,
        value: fast[index].value - slow[index].value,
      }}));
      const signal = marketEmaPoints(macdSeries.map((point) => ({{ close: point.value, time: point.time }})), 9);
      const latest = macdSeries[macdSeries.length - 1].value;
      const previous = macdSeries[macdSeries.length - 2].value;
      const latestSignal = signal[signal.length - 1].value;
      const previousSignal = signal[signal.length - 2].value;
      const histogram = latest - latestSignal;
      const crossedUp = previous <= previousSignal && latest > latestSignal;
      const crossedDown = previous >= previousSignal && latest < latestSignal;
      if (crossedUp) {{
        return {{ tone: "up", text: "bull cross", detail: "MACD crossed above signal" }};
      }}
      if (crossedDown) {{
        return {{ tone: "down", text: "bear cross", detail: "MACD crossed below signal" }};
      }}
      if (histogram > 0) {{
        return {{ tone: "up", text: "bullish", detail: "MACD above signal" }};
      }}
      if (histogram < 0) {{
        return {{ tone: "down", text: "bearish", detail: "MACD below signal" }};
      }}
      return {{ tone: "neutral", text: "flat", detail: "MACD near signal" }};
    }}

    function marketBollingerPositionState(candles, period, deviations) {{
      const points = marketBollingerPoints(candles, period, deviations);
      const latestBand = [...points].reverse().find((point) => point.upper !== null && point.lower !== null);
      if (!latestBand || candles.length < period) {{
        return {{ tone: "", text: "pending", detail: "Not enough Bollinger data" }};
      }}
      const latest = candles[candles.length - 1];
      const widthPct = latestBand.basis ? ((latestBand.upper - latestBand.lower) / latestBand.basis) * 100 : 0;
      if (latest.close >= latestBand.upper) {{
        return {{ tone: "warn", text: "upper touch", detail: "Price at/above upper band; width " + widthPct.toFixed(1) + "%" }};
      }}
      if (latest.close <= latestBand.lower) {{
        return {{ tone: "cool", text: "lower touch", detail: "Price at/below lower band; width " + widthPct.toFixed(1) + "%" }};
      }}
      if (widthPct <= 10) {{
        return {{ tone: "neutral", text: "squeeze", detail: "Band width " + widthPct.toFixed(1) + "%" }};
      }}
      if (latest.close >= latestBand.basis) {{
        return {{ tone: "up", text: "above basis", detail: "Price above Bollinger basis; width " + widthPct.toFixed(1) + "%" }};
      }}
      return {{ tone: "down", text: "below basis", detail: "Price below Bollinger basis; width " + widthPct.toFixed(1) + "%" }};
    }}

    function marketVolumeSpikeState(candles, period) {{
      const parsedPeriod = Number(period || 20);
      if (candles.length <= parsedPeriod) {{
        return {{ tone: "", text: "pending", detail: "Not enough volume data" }};
      }}
      const latest = candles[candles.length - 1];
      const windowCandles = candles.slice(candles.length - parsedPeriod - 1, candles.length - 1);
      const average = windowCandles.reduce((total, candle) => total + (candle.volume || 0), 0) / parsedPeriod;
      if (!average) {{
        return {{ tone: "", text: "no volume", detail: "Volume unavailable" }};
      }}
      const multiple = latest.volume / average;
      if (multiple >= 3) {{
        return {{ tone: "warn", text: multiple.toFixed(1) + "x spike", detail: "Latest volume vs " + parsedPeriod + "-candle average" }};
      }}
      if (multiple >= 1.5) {{
        return {{ tone: "up", text: multiple.toFixed(1) + "x active", detail: "Latest volume vs " + parsedPeriod + "-candle average" }};
      }}
      if (multiple <= 0.55) {{
        return {{ tone: "cool", text: multiple.toFixed(1) + "x quiet", detail: "Latest volume vs " + parsedPeriod + "-candle average" }};
      }}
      return {{ tone: "neutral", text: multiple.toFixed(1) + "x normal", detail: "Latest volume vs " + parsedPeriod + "-candle average" }};
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

    function marketRsiCard(id, value, state, updatedAt) {{
      const element = document.getElementById(id);
      if (!element) {{
        return;
      }}
      const valueElement = element.querySelector('[data-indicator-value="rsi"]');
      const stateElement = element.querySelector('[data-indicator-state="rsi"]');
      if (valueElement) {{
        valueElement.textContent = value === null ? "RSI --" : "RSI " + value.toFixed(1);
      }}
      if (stateElement) {{
        const updated = updatedAt ? " at " + new Date(updatedAt).toLocaleTimeString() : "";
        stateElement.textContent = state.text + updated;
      }}
      element.title = state.detail;
      element.setAttribute("aria-label", state.text + ": " + state.detail);
      element.className = "market-indicator-card" + (state.tone ? " " + state.tone : "");
    }}

    function marketIndicatorRow(cardId, key, state) {{
      const card = document.getElementById(cardId);
      if (!card) {{
        return;
      }}
      const row = card.querySelector('[data-indicator-row="' + key + '"]');
      if (!row) {{
        return;
      }}
      const value = row.querySelector("strong");
      if (value) {{
        value.textContent = state.text;
        value.title = state.detail;
      }}
      row.className = "market-indicator-row" + (state.tone ? " " + state.tone : "");
      row.setAttribute("aria-label", key + ": " + state.detail);
    }}

    function marketUpdateIndicatorRows(cardId, candles) {{
      marketIndicatorRow(cardId, "macd", marketMacdState(candles));
      marketIndicatorRow(cardId, "bb", marketBollingerPositionState(candles, 20, 2));
      marketIndicatorRow(cardId, "volume", marketVolumeSpikeState(candles, 20));
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

    function marketPathFromPoints(points, xValue, yValue, valueKey) {{
      let started = false;
      return points
        .map((point, index) => {{
          const value = point[valueKey];
          if (value === null || value === undefined) {{
            started = false;
            return "";
          }}
          const command = started ? "L" : "M";
          started = true;
          return command + xValue(index).toFixed(1) + " " + yValue(value).toFixed(1);
        }})
        .filter(Boolean)
        .join(" ");
    }}

    function drawMarketCandles(rows, chartId, statusId, trendId, rsiId, labelText, emaPeriod, axisMode, bollingerConfig, rsiCardId) {{
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
        marketRsiCard(rsiCardId, null, {{ tone: "", text: "RSI pending", detail: "Not enough candle data" }}, null);
        ["macd", "bb", "volume"].forEach((key) => marketIndicatorRow(rsiCardId, key, {{ tone: "", text: "pending", detail: "Not enough candle data" }}));
        marketSignalWatch(labelText, {{ tone: "", text: "Not enough data" }});
        return;
      }}
      const bollingerPoints = bollingerConfig
        ? marketBollingerPoints(candles, bollingerConfig.period, bollingerConfig.deviations)
        : [];
      const width = 720;
      const height = 230;
      const leftPad = 24;
      const rightPad = 70;
      const topPad = 18;
      const bottomPad = 34;
      const bandHighs = bollingerPoints.map((point) => point.upper).filter((value) => value !== null);
      const bandLows = bollingerPoints.map((point) => point.lower).filter((value) => value !== null);
      const highs = candles.map((row) => row.high).concat(bandHighs);
      const lows = candles.map((row) => row.low).concat(bandLows);
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

      if (bollingerPoints.length > 1) {{
        const xBand = (index) => leftPad + step * index + step / 2;
        const upperPoints = bollingerPoints.filter((point) => point.upper !== null);
        if (upperPoints.length > 1) {{
          const upperPath = marketPathFromPoints(bollingerPoints, xBand, y, "upper");
          const reversedLowerPath = bollingerPoints
            .map((point, index) => ({{ point, index }}))
            .filter((item) => item.point.lower !== null)
            .reverse()
            .map((item) => "L" + xBand(item.index).toFixed(1) + " " + y(item.point.lower).toFixed(1))
            .join(" ");
          const bandFill = document.createElementNS(ns, "path");
          bandFill.setAttribute("d", upperPath + " " + reversedLowerPath + " Z");
          bandFill.setAttribute("class", "market-bollinger-fill");
          svg.appendChild(bandFill);

          [
            ["upper", "market-bollinger-line"],
            ["basis", "market-bollinger-line market-bollinger-mid"],
            ["lower", "market-bollinger-line"],
          ].forEach((entry) => {{
            const path = document.createElementNS(ns, "path");
            path.setAttribute("d", marketPathFromPoints(bollingerPoints, xBand, y, entry[0]));
            path.setAttribute("class", entry[1]);
            svg.appendChild(path);
          }});

          const bandLabel = document.createElementNS(ns, "text");
          bandLabel.textContent = "BB" + String(bollingerConfig.period) + " " + String(bollingerConfig.deviations) + "sd";
          bandLabel.setAttribute("x", String(leftPad + 58));
          bandLabel.setAttribute("y", String(topPad + 4));
          bandLabel.setAttribute("class", "market-bollinger-label");
          svg.appendChild(bandLabel);
        }}
      }}

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
      const latest = candles[candles.length - 1];
      const rsiValue = marketRsiValue(candles, 14);
      const rsiState = marketRsiState(candles, 14);
      marketRsiBadge(rsiId, rsiState);
      marketRsiCard(rsiCardId, rsiValue, rsiState, latest.time);
      marketUpdateIndicatorRows(rsiCardId, candles);
      marketSignalWatch(labelText, marketSignalState(candles, emaPoints, rsiValue));

      const latestBand = [...bollingerPoints].reverse().find((point) => point.upper !== null && point.lower !== null);
      const bandText = latestBand ? " · BB width " + formatMarketSignedPercent(((latestBand.upper - latestBand.lower) / latest.close) * 100).replace("+", "") : "";
      marketText(statusId, labelText + " candles updated at " + new Date(latest.time).toLocaleTimeString() + bandText);
    }}

    function drawMarketCrossChart(seriesRows, chartId, statusId, axisMode, independentRange) {{
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
        const candles = independentRange ? item.candles : item.candles.slice(-minLength);
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
      const timeValues = normalized.flatMap((item) => item.points.map((point) => point.time));
      const timeStart = Math.min(...timeValues);
      const timeEnd = Math.max(...timeValues);
      const step = chartWidth / Math.max(1, minLength - 1);
      const x = (index) => leftPad + step * index;
      const xTime = (time) => leftPad + ((time - timeStart) / (timeEnd - timeStart || 1)) * chartWidth;
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

      const axisPoints = independentRange
        ? [
            {{ time: timeStart, x: leftPad, anchor: "start" }},
            {{ time: timeStart + (timeEnd - timeStart) / 2, x: leftPad + chartWidth / 2, anchor: "middle" }},
            {{ time: timeEnd, x: width - rightPad, anchor: "end" }},
          ]
        : [0, Math.floor((minLength - 1) / 2), minLength - 1].map((index) => ({{
            time: normalized[0].points[index].time,
            x: x(index),
            anchor: index === 0 ? "start" : index === minLength - 1 ? "end" : "middle",
          }}));
      axisPoints.forEach((point) => {{
        const label = document.createElementNS(ns, "text");
        label.textContent = marketAxisTimeLabel(point.time, axisMode);
        label.setAttribute("x", String(point.x));
        label.setAttribute("y", String(height - 9));
        label.setAttribute("text-anchor", point.anchor);
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
          .map((point, index) => {{
            const pointX = independentRange ? xTime(point.time) : x(index);
            return (index === 0 ? "M" : "L") + pointX.toFixed(1) + " " + y(point.value).toFixed(1);
          }})
          .join(" ");
        path.setAttribute("d", d);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", item.color);
        path.setAttribute("stroke-width", "3");
        path.setAttribute("stroke-linecap", "round");
        path.setAttribute("stroke-linejoin", "round");
        svg.appendChild(path);

        const latest = item.points[item.points.length - 1];
        const latestX = independentRange ? xTime(latest.time) : x(item.points.length - 1);
        const marker = document.createElementNS(ns, "circle");
        marker.setAttribute("cx", String(latestX));
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

    function marketRelativeStrengthState(seriesRows) {{
      const series = seriesRows
        .map((item) => ({{
          label: item.label,
          candles: marketCandlesFromRows(item.rows),
        }}))
        .filter((item) => item.candles.length >= 2);
      const kas = series.find((item) => item.label === "KAS/USDT");
      const btc = series.find((item) => item.label === "BTC/USDT");
      if (!kas || !btc) {{
        return {{ tone: "", text: "pending", detail: "Not enough KAS/BTC data" }};
      }}
      const length = Math.min(kas.candles.length, btc.candles.length);
      if (length < 2) {{
        return {{ tone: "", text: "pending", detail: "Not enough KAS/BTC data" }};
      }}
      const kasCandles = kas.candles.slice(-length);
      const btcCandles = btc.candles.slice(-length);
      const kasChange = ((kasCandles[length - 1].close / (kasCandles[0].close || 1)) - 1) * 100;
      const btcChange = ((btcCandles[length - 1].close / (btcCandles[0].close || 1)) - 1) * 100;
      const relative = kasChange - btcChange;
      const text = (relative >= 0 ? "+" : "") + relative.toFixed(1) + "%";
      if (relative >= 3) {{
        return {{ tone: "up", text, detail: "KAS outperforming BTC by " + text }};
      }}
      if (relative <= -3) {{
        return {{ tone: "down", text, detail: "KAS underperforming BTC by " + text }};
      }}
      return {{ tone: "neutral", text, detail: "KAS vs BTC relative change " + text }};
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
      if (!marketShouldRefresh("kline:" + config.label, config.refreshMs)) {{
        return;
      }}
      try {{
        const payload = await fetchMarketJson(marketKlineUrl(config));
        drawMarketCandles(((payload.result || {{}}).list || []), config.chartId, config.statusId, config.trendId, config.rsiId, config.label, config.emaPeriod, config.axisMode, config.bollinger, config.rsiCardId);
        marketSourceStatus("spot-" + config.label, "Spot " + config.label, payload.fromCache ? "cached" : "ok", marketSourceDetail(payload));
      }} catch (error) {{
        marketText(config.statusId, "KAS/USDT " + config.label + " candles unavailable");
        marketTrendBadge(config.trendId, {{ tone: "", text: "Trend pending", detail: "Market candles unavailable" }});
        marketRsiBadge(config.rsiId, {{ tone: "", text: "RSI pending", detail: "Market candles unavailable" }});
        marketRsiCard(config.rsiCardId, null, {{ tone: "", text: "Unavailable", detail: "Market candles unavailable" }}, null);
        ["macd", "bb", "volume"].forEach((key) => marketIndicatorRow(config.rsiCardId, key, {{ tone: "", text: "unavailable", detail: "Market candles unavailable" }}));
        marketSignalWatch(config.label, {{ tone: "", text: "Unavailable" }});
        marketSourceStatus("spot-" + config.label, "Spot " + config.label, "fail", marketErrorDetail(error));
      }}
    }}

    async function refreshMarketCrossChart(config) {{
      if (!marketShouldRefresh("cross:" + config.label, config.refreshMs)) {{
        return;
      }}
      try {{
        const payloads = await Promise.all(config.series.map((item) => fetchMarketJson(item.url)));
        const seriesRows = payloads.map((payload, index) => ({{
          label: config.series[index].label,
          color: config.series[index].color,
          rows: ((payload.result || {{}}).list || []),
        }}));
        drawMarketCrossChart(seriesRows, config.chartId, config.statusId, config.axisMode, config.independentRange);
        marketIndicatorRow(config.indicatorCardId, "relative", marketRelativeStrengthState(seriesRows));
        marketSourceStatus("cross-" + config.label, "KAS/BTC cross " + config.label, payloads.some((payload) => payload.fromCache) ? "cached" : "ok", "2/2 series " + marketSourceDetail(payloads[0]));
      }} catch (error) {{
        marketText(config.statusId, "KAS/BTC " + config.label + " cross unavailable");
        marketIndicatorRow(config.indicatorCardId, "relative", {{ tone: "", text: "unavailable", detail: "KAS/BTC cross unavailable" }});
        marketSourceStatus("cross-" + config.label, "KAS/BTC cross " + config.label, "fail", marketErrorDetail(error));
      }}
    }}

    async function refreshMarketVolumeChart() {{
      if (!marketShouldRefresh("volume", marketConfig.volume.refreshMs)) {{
        return;
      }}
      const sourceRows = await Promise.all(marketConfig.volume.sources.map(async (source) => {{
        try {{
          const payload = await fetchMarketJson(source.url);
          return {{
            label: source.label,
            color: source.color,
            rows: marketVolumeRows(payload, source.parser),
            cached: Boolean(payload.fromCache),
          }};
        }} catch (error) {{
          return {{
            label: source.label,
            color: source.color,
            rows: [],
            cached: false,
            error: marketErrorDetail(error),
          }};
        }}
      }}));
      drawMarketVolumeChart(sourceRows, marketConfig.volume);
      const availableSources = sourceRows.filter((source) => source.rows.length > 0).length;
      const cachedSources = sourceRows.filter((source) => source.cached).length;
      const failedSources = sourceRows.filter((source) => source.rows.length === 0);
      const failedDetail = failedSources.length ? "; failed " + failedSources.slice(0, 3).map((source) => source.label).join(", ") : "";
      marketSourceStatus("volume", "Exchange volume", availableSources > 0 ? (cachedSources > 0 ? "cached" : "ok") : "fail", availableSources + "/" + marketConfig.volume.sources.length + " venues" + failedDetail);
    }}

    async function refreshLiquidationMap(config) {{
      if (!marketShouldRefresh("liquidation:" + config.label, config.refreshMs)) {{
        return;
      }}
      try {{
        const payloads = await Promise.all([
          fetchMarketJson(config.klineUrl),
          fetchMarketJson(config.openInterestUrl),
        ]);
        drawLiquidationMap(payloads[0], payloads[1], config);
        marketSourceStatus("liquidation-" + config.label, "Liquidation " + config.label, payloads.some((payload) => payload.fromCache) ? "cached" : "ok", marketSourceDetail(payloads[0]));
      }} catch (error) {{
        marketText(config.statusId, "KAS/USDT futures liquidation map unavailable");
        marketSourceStatus("liquidation-" + config.label, "Liquidation " + config.label, "fail", marketErrorDetail(error));
      }}
    }}

    async function refreshFuturesPositioning() {{
      if (!marketShouldRefresh("futures-positioning", 30 * 1000)) {{
        return;
      }}
      try {{
        const payload = await fetchMarketJson(marketConfig.futuresTickerUrl);
        const ticker = ((payload.result || {{}}).list || [])[0] || {{}};
        const markPrice = marketNumber(ticker.markPrice || ticker.lastPrice);
        const indexPrice = marketNumber(ticker.indexPrice);
        const fundingRate = marketNumber(ticker.fundingRate);
        const fundingInterval = marketNumber(ticker.fundingIntervalHour) || 4;
        const openInterest = marketNumber(ticker.openInterest);
        const volume24h = marketNumber(ticker.volume24h);
        const basisPct = markPrice !== null && indexPrice ? ((markPrice - indexPrice) / indexPrice) * 100 : null;
        const fundingApr = fundingRate !== null ? fundingRate * (24 / fundingInterval) * 365 * 100 : null;
        const oiVolumeRatio = openInterest !== null && volume24h ? openInterest / volume24h : null;
        const risk = marketPositioningRisk(null, oiVolumeRatio, basisPct, null);
        marketText("futures-mark", formatMarketPrice(markPrice));
        marketText("futures-index", formatMarketPrice(indexPrice));
        marketText("futures-basis", basisPct === null ? "unknown" : formatMarketSignedPercent(basisPct));
        marketText("futures-funding", formatFundingPercent(ticker.fundingRate));
        marketText("futures-funding-apr", fundingApr === null ? "unknown" : formatMarketSignedPercent(fundingApr));
        marketText("futures-next-funding", formatMarketTime(ticker.nextFundingTime));
        marketText("futures-open-interest", formatMarketVolume(ticker.openInterest));
        marketText("futures-open-interest-value", formatMarketUsdt(ticker.openInterestValue));
        marketText("futures-volume", formatMarketVolume(ticker.volume24h));
        marketText("futures-risk", risk.level + " / " + risk.score);
        marketText("futures-risk-reasons", risk.reasons.length ? risk.reasons.join(", ") : "none");
        const updatedPrefix = payload.fromCache ? "cached " : "";
        marketText("futures-status", updatedPrefix + "linear perp updated at " + new Date(Number(payload.cachedAt || payload.time || Date.now())).toLocaleTimeString());
        marketSourceStatus("futures-positioning", "Futures positioning", payload.fromCache ? "cached" : "ok", marketSourceDetail(payload));
      }} catch (error) {{
        marketText("futures-status", "KAS/USDT futures positioning unavailable");
        marketSourceStatus("futures-positioning", "Futures positioning", "fail", marketErrorDetail(error));
      }}
    }}

    async function refreshFuturesTrend() {{
      if (!marketShouldRefresh("futures-trend", marketConfig.futuresTrend.refreshMs)) {{
        return;
      }}
      try {{
        const payloads = await Promise.all([
          fetchMarketJson(marketConfig.futuresTrend.openInterestUrl),
          fetchMarketJson(marketConfig.futuresTrend.fundingUrl),
        ]);
        drawFuturesTrend(payloads[0], payloads[1], marketConfig.futuresTrend);
        marketSourceStatus("futures-trend", "Futures trend", payloads.some((payload) => payload.fromCache) ? "cached" : "ok", marketSourceDetail(payloads[0]));
      }} catch (error) {{
        marketText(marketConfig.futuresTrend.statusId, "KAS/USDT futures trend unavailable");
        marketSourceStatus("futures-trend", "Futures trend", "fail", marketErrorDetail(error));
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
        marketSourceStatus("spot-ticker", "Spot ticker", tickerPayload.fromCache ? "cached" : "ok", marketSourceDetail(tickerPayload));
      }} catch (error) {{
        marketText("market-last", "Unavailable");
        marketText("market-change", "Market API unavailable");
        marketSourceStatus("spot-ticker", "Spot ticker", "fail", marketErrorDetail(error));
      }}
      await Promise.all([
        ...marketConfig.klines.map(refreshMarketChart),
        ...marketConfig.cross.map(refreshMarketCrossChart),
        refreshMarketVolumeChart(),
        refreshFuturesPositioning(),
        refreshFuturesTrend(),
        ...marketConfig.liquidations.map(refreshLiquidationMap),
      ]);
    }}

    marketRenderSourceStates();
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
    stream_page_path = Path(config.get("stream_page_path") or DEFAULT_CONFIG["stream_page_path"])
    canvas_stream_page = config.get("canvas_stream_page_path") or DEFAULT_CONFIG["canvas_stream_page_path"]
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    history_db_path = sqlite_history_path(config)
    market_snapshot_path = Path(config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"])
    metrics_path = Path(config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"])
    benchmark_summary = build_benchmark_summary(benchmark_path, limit=48)
    thresholds = config.get("thresholds", {})
    repeat_minutes = float(thresholds.get("alert_repeat_minutes", 60))
    state = load_state(state_path)
    report = build_report(config)
    apply_stateful_checks(report, state, config)
    wallet_event = apply_wallet_change_detection(report, state, config)
    new_wallet_events = update_wallet_event_state(state, report, config)
    (report.get("wallet") or {})["events"] = list(state.get("wallet_events") or [])
    (report.get("wallet") or {})["new_events"] = new_wallet_events
    update_whale_confirmed_candidates(state, report, config)
    new_whale_events = update_whale_event_state(state, report, config)
    whale_event = "whale_tx_detected" if new_whale_events and bool(whale_watch_config(config).get("alert_enabled", True)) else None
    indexer_watch_event = apply_indexer_watchlist(report, state, config)
    new_sdk_events = update_sdk_subscription_event_state(state, report, config)
    sdk_watch_event = "sdk_watch_event" if new_sdk_events and bool(sdk_probe_config(config).get("alert_enabled", True)) else None
    apply_wallet_policy_checks(report, config)
    incident_event = update_incident_state(state, report)
    enrich_operational_fields(report, config, state)
    previous_status = state.get("status")
    previous_severity = state.get("severity")
    previous_report = state.get("last_report") or {}
    previous_synced = (previous_report.get("grpc_metrics") or {}).get("is_synced")
    current_synced = (report.get("grpc_metrics") or {}).get("is_synced")
    sync_completed = previous_synced is False and current_synced is True
    previous_indexer = previous_report.get("indexer") or {}
    current_indexer = report.get("indexer") or {}
    indexer_ready = (
        bool(previous_indexer.get("enabled"))
        and bool(current_indexer.get("enabled"))
        and previous_indexer.get("state") == "syncing"
        and current_indexer.get("state") == "up"
    )
    event = sdk_watch_event or indexer_watch_event or whale_event or wallet_event or (
        "indexer_ready" if indexer_ready else ("sync_completed" if sync_completed else incident_event)
    )
    should_emit = (
        should_emit_alert(state, report, repeat_minutes)
        or sync_completed
        or indexer_ready
        or bool(wallet_event)
        or bool(whale_event)
        or bool(indexer_watch_event)
        or bool(sdk_watch_event)
    )
    if alert_muted_by_maintenance(report):
        should_emit = False
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
            "maintenance": report.get("maintenance"),
        }
    )
    if should_emit:
        state["last_alert_at"] = report["checked_at"]
    save_state(state_path, state)
    recovery_records = recent_recovery_records(config)
    write_status_page(status_page_path, report, state, benchmark_path, recovery_records, history_db_path, market_snapshot_path)
    if canvas_status_page:
        write_status_page(Path(canvas_status_page), report, state, benchmark_path, recovery_records, history_db_path, market_snapshot_path)
    write_stream_page(stream_page_path, report, state, benchmark_path, market_snapshot_path)
    if canvas_stream_page:
        write_stream_page(Path(canvas_stream_page), report, state, benchmark_path, market_snapshot_path)
    recovery_summary = build_recovery_summary(recovery_history_path(config))
    market_metrics = build_market_metrics(market_snapshot_path)
    write_prometheus_metrics(metrics_path, report, benchmark_summary, recovery_summary, market_metrics)

    if should_emit:
        print(format_alert(report, previous_status, previous_severity, event=event))
    return 0 if report["status"] == "ok" else 1


def stream_page(config: dict) -> int:
    report, state = build_stateful_report(config)
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    market_snapshot_path = Path(config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"])
    stream_page_path = Path(config.get("stream_page_path") or DEFAULT_CONFIG["stream_page_path"])
    canvas_stream_page = config.get("canvas_stream_page_path") or DEFAULT_CONFIG["canvas_stream_page_path"]
    write_stream_page(stream_page_path, report, state, benchmark_path, market_snapshot_path)
    if canvas_stream_page:
        write_stream_page(Path(canvas_stream_page), report, state, benchmark_path, market_snapshot_path)
    print(f"Stream page written: {stream_page_path}")
    if canvas_stream_page:
        print(f"Canvas stream page written: {canvas_stream_page}")
    return 0 if report["status"] == "ok" else 1


def format_alert(
    report: dict[str, Any],
    previous_status: str | None = None,
    previous_severity: str | None = None,
    event: str | None = None,
) -> str:
    failed_checks = [check for check in report["checks"] if not check["ok"]]
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    if event == "sync_completed":
        title = f"Kaspa watchtower: {report['node_name']} sync completed"
    elif event == "indexer_ready":
        title = f"Kaspa watchtower: {report['node_name']} indexer ready"
    elif event == "whale_tx_detected":
        title = f"Kaspa watchtower: {report['node_name']} whale tx detected"
    elif event == "indexer_watch_event":
        title = f"Kaspa watchtower: {report['node_name']} watched address tx"
    elif event == "sdk_watch_event":
        title = f"Kaspa watchtower: {report['node_name']} SDK watched address tx"
    elif event == "wallet_large_outgoing":
        title = f"Kaspa watchtower: {report['node_name']} large wallet outgoing"
    elif event == "wallet_changed":
        title = f"Kaspa watchtower: {report['node_name']} wallet changed"
    elif event == "incident_resolved":
        title = f"Kaspa watchtower: {report['node_name']} recovered"
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
        f"health_score={report.get('health_score', 'unknown')}",
    ]
    if previous_status:
        lines.append(f"previous_status={previous_status}")
    if previous_severity:
        lines.append(f"previous_severity={previous_severity}")

    if failed_checks:
        causes = report.get("failure_causes") or check_failure_causes(report)
        lines.append(f"cause_guess={', '.join(causes) if causes else 'unknown'}")
        duration = numeric(incident.get("duration_seconds"))
        if duration is not None:
            lines.append(f"incident_duration={duration / 60:.1f}m started_at={incident.get('started_at')}")
        lines.append("원인:")
        for check in failed_checks:
            lines.append(f"- {check['name']}: {check['detail']}")
    elif event == "sync_completed":
        lines.append("상태: mainnet sync completed")
        lines.append("Next: set thresholds.require_synced=true for strict production monitoring")
    elif event == "indexer_ready":
        indexer = report.get("indexer") or {}
        metrics = indexer.get("metrics") or {}
        lines.append("상태: indexer catch-up completed")
        lines.append(
            "Indexer: "
            f"state={indexer.get('state', 'unknown')} "
            f"checkpoint_age={metrics.get('checkpoint_age_seconds', 'unknown')} "
            f"lag={metrics.get('lag_seconds', 'unknown')}"
        )
        lines.append("Next: enable indexer watchlist alerts for production addresses")
    elif event == "whale_tx_detected":
        whale = report.get("whale_watch") or {}
        new_events = whale.get("new_events") or whale.get("candidates") or []
        lines.append(
            "Whale: "
            f"threshold={format_kas(whale.get('min_amount_sompi'))} "
            f"new_events={len(new_events)}"
        )
        for item in new_events[:5]:
            tx_id = str(item.get("tx_id") or "unknown")
            short_tx = tx_id if len(tx_id) <= 18 else f"{tx_id[:10]}...{tx_id[-6:]}"
            address = str(item.get("address") or "")
            short_address = address if len(address) <= 24 else f"{address[:12]}...{address[-8:]}"
            line = (
                f"- amount={format_kas(item.get('amount_sompi'))} "
                f"tx={short_tx} address={short_address or 'unknown'} source={item.get('source', 'unknown')}"
            )
            if item.get("tx_url"):
                line += f" link={item.get('tx_url')}"
            lines.append(line)
    elif event == "indexer_watch_event":
        watch = report.get("indexer_watch") or {}
        new_events = watch.get("new_events") or []
        lines.append(
            "Indexer watch: "
            f"watched={len(watch.get('watch_addresses') or [])} "
            f"new_events={len(new_events)} "
            f"total_events={len(watch.get('events') or [])}"
        )
        for item in new_events[:5]:
            lines.append(format_watch_event_line(item, default_source="indexer"))
    elif event == "sdk_watch_event":
        sdk = report.get("sdk_metrics") or {}
        new_events = sdk.get("new_events") or []
        lines.append(
            "SDK watch: "
            f"watched={sdk.get('subscription_watch_addresses', 0)} "
            f"new_events={len(new_events)} "
            f"total_events={len(sdk.get('events') or [])}"
        )
        for item in new_events[:5]:
            lines.append(format_watch_event_line(item, default_source="sdk_subscription"))
    elif event in {"wallet_changed", "wallet_large_outgoing"}:
        wallet = report.get("wallet") or {}
        change = wallet.get("change") or {}
        alert_items = change.get("large_outgoing_entries") if event == "wallet_large_outgoing" else change.get("alert_entries")
        alert_items = alert_items or change.get("entries") or []
        lines.append(
            "Wallet: "
            f"total={format_kas(wallet.get('total_sompi'))} "
            f"delta={format_kas(change.get('total_delta_sompi'))}"
        )
        for item in alert_items[:5]:
            label = item.get("label") or "unlabeled"
            address = str(item.get("address") or "unknown")
            short_address = address if len(address) <= 24 else f"{address[:12]}...{address[-8:]}"
            lines.append(f"- {label}: delta={format_kas(item.get('delta_sompi'))} {short_address}")
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
    if maintenance.get("active"):
        lines.append(
            "Maintenance: "
            f"active critical_only={maintenance.get('critical_only')} "
            f"until={maintenance.get('mute_until') or 'manual'} "
            f"reason={maintenance.get('reason') or 'none'}"
        )
    return "\n".join(lines)


def format_summary(report: dict[str, Any]) -> str:
    grpc_metrics = report.get("grpc_metrics") or {}
    indexer = report.get("indexer") or {}
    indexer_metrics = indexer.get("metrics") or {}
    indexer_watch = report.get("indexer_watch") or {}
    progress = report.get("progress") or {}
    sync_progress = report.get("sync_progress") or {}
    disk = report.get("disk") or {}
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    failed = failed_check_names(report)
    failed_text = ", ".join(failed) if failed else "none"
    causes = report.get("failure_causes") or check_failure_causes(report)
    causes_text = ", ".join(causes) if causes else "none"
    incident_duration = numeric(incident.get("duration_seconds"))
    incident_duration_text = "inactive" if incident_duration is None else f"{incident_duration / 60:.1f}m"
    latest_relay_age = progress.get("latest_relay_age_seconds")
    latest_relay_age_text = "unknown" if latest_relay_age is None else f"{latest_relay_age}s"
    disk_text = "unknown"
    if disk.get("exists"):
        disk_text = f"{disk.get('free_gb')} GiB ({disk.get('free_percent')}%)"
    indexer_enabled = bool(indexer.get("enabled", False))
    indexer_state = indexer.get("state", "unknown")
    indexer_ok = indexer.get("ok", False)
    indexer_health = indexer.get("health_ok", False)
    indexer_metrics_ok = indexer.get("metrics_ok", False)
    indexer_lag = indexer_metrics.get("lag_seconds", "unknown")
    indexer_checkpoint_age = indexer_metrics.get("checkpoint_age_seconds", "unknown")
    if not indexer_enabled:
        indexer_state = "disabled"
        indexer_ok = True
        indexer_health = "skipped"
        indexer_metrics_ok = "skipped"
        indexer_lag = "disabled"
        indexer_checkpoint_age = "disabled"

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
        (
            "indexer="
            f"enabled={indexer_enabled} "
            f"state={indexer_state} "
            f"ok={indexer_ok} "
            f"health={indexer_health} "
            f"syncing={indexer.get('syncing', False)} "
            f"metrics={indexer_metrics_ok} "
            f"lag={indexer_lag} "
            f"checkpoint_age={indexer_checkpoint_age}"
        ),
        (
            "indexer_watch="
            f"enabled={indexer_watch.get('enabled', False)} "
            f"ok={indexer_watch.get('ok', False)} "
            f"addresses={len(indexer_watch.get('watch_addresses') or [])} "
            f"events={len(indexer_watch.get('events') or [])} "
            f"new={len(indexer_watch.get('new_events') or [])}"
        ),
        f"disk_free={disk_text}",
        (
            "ops="
            f"health_score={report.get('health_score', 'unknown')} "
            f"incident_duration={incident_duration_text} "
            f"maintenance_active={maintenance.get('active', False)} "
            f"maintenance_until={maintenance.get('mute_until') or 'manual' if maintenance.get('active') else 'none'}"
        ),
        f"cause_guess={causes_text}",
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


def format_discord_status(report: dict[str, Any]) -> str:
    grpc_metrics = report.get("grpc_metrics") or {}
    indexer = report.get("indexer") or {}
    indexer_metrics = indexer.get("metrics") or {}
    wallet = report.get("wallet") or {}
    wallet_change = wallet.get("change") or {}
    mining = report.get("mining") or {}
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    failed = failed_check_names(report)
    failed_text = ", ".join(failed) if failed else "none"
    incident_duration = numeric(incident.get("duration_seconds"))
    incident_text = "inactive" if incident_duration is None else f"{incident_duration / 60:.1f}m"
    maintenance_text = "active" if maintenance.get("active") else "off"
    causes = report.get("failure_causes") or check_failure_causes(report)
    causes_text = ", ".join(causes) if causes else "none"
    return "\n".join(
        [
            f"Kaspa status: {report.get('node_name', 'unknown')}",
            (
                f"status={report.get('status', 'unknown')} "
                f"severity={report.get('severity', 'unknown')} "
                f"health_score={report.get('health_score', 'unknown')}"
            ),
            (
                "node="
                f"network={grpc_metrics.get('network_id', 'unknown')} "
                f"synced={grpc_metrics.get('is_synced', 'unknown')} "
                f"peers={grpc_metrics.get('peer_count', 'unknown')} "
                f"active={grpc_metrics.get('active_peers', 'unknown')} "
                f"daa={grpc_metrics.get('virtual_daa_score', 'unknown')}"
            ),
            (
                "ops="
                f"incident={incident_text} "
                f"maintenance={maintenance_text} "
                f"maintenance_until={maintenance.get('mute_until') or 'none'}"
            ),
            (
                "wallet="
                f"enabled={wallet.get('enabled', False)} "
                f"ok={wallet.get('ok', False)} "
                f"addresses={len(wallet.get('entries') or [])} "
                f"total={format_kas(wallet.get('total_sompi'))} "
                f"delta={format_kas(wallet_change.get('total_delta_sompi'))}"
            ),
            (
                "mining="
                f"enabled={mining.get('enabled', False)} "
                f"running={mining.get('running', False)} "
                f"hashrate={format_hashrate_local(mining.get('hashrate_hs'))}"
            ),
            (
                "indexer="
                f"enabled={indexer.get('enabled', False)} "
                f"state={indexer.get('state', 'unknown')} "
                f"ok={indexer.get('ok', False)} "
                f"lag={indexer_metrics.get('lag_seconds', 'unknown')} "
                f"checkpoint_age={indexer_metrics.get('checkpoint_age_seconds', 'unknown')}"
            ),
            f"cause_guess={causes_text}",
            f"failed_checks={failed_text}",
        ]
    )


def format_discord_wallet(report: dict[str, Any]) -> str:
    wallet = report.get("wallet") or {}
    change = wallet.get("change") or {}
    entries = wallet.get("entries") or []
    lines = [
        f"Kaspa wallet watch: {report.get('node_name', 'unknown')}",
        (
            f"enabled={wallet.get('enabled', False)} "
            f"configured={wallet.get('configured', False)} "
            f"ok={wallet.get('ok', False)} "
            f"addresses={len(entries)} "
            f"total={format_kas(wallet.get('total_sompi'))}"
        ),
        (
            "change="
            f"changed={change.get('changed', False)} "
            f"delta={format_kas(change.get('total_delta_sompi'))}"
        ),
        f"detail={wallet.get('detail', 'unknown')}",
    ]
    for entry in entries[:10]:
        label = entry.get("label") or "unlabeled"
        address = str(entry.get("address") or "unknown")
        short_address = address if len(address) <= 24 else f"{address[:12]}...{address[-8:]}"
        error = f" error={entry.get('error')}" if entry.get("error") else ""
        lines.append(f"- {label}: {format_kas(entry.get('balance_sompi'))} {short_address}{error}")
    if len(entries) > 10:
        lines.append(f"- ... {len(entries) - 10} more addresses")
    return "\n".join(lines)


def format_discord_wallet_txs(report: dict[str, Any]) -> str:
    wallet = report.get("wallet") or {}
    pending = wallet.get("pending") or {}
    pending_entries = pending.get("entries") or []
    events = list(wallet.get("events") or [])
    lines = [
        f"Kaspa wallet txs: {report.get('node_name', 'unknown')}",
        (
            f"pending_ok={pending.get('ok', False)} "
            f"pending={len(pending_entries)} "
            f"events={len(events)}"
        ),
    ]
    if pending.get("detail") or pending.get("error"):
        lines.append(f"pending_detail={pending.get('detail') or pending.get('error')}")
    if pending_entries:
        lines.append("pending:")
    for entry in pending_entries[:8]:
        address = str(entry.get("address") or "unknown")
        short_address = address if len(address) <= 24 else f"{address[:12]}...{address[-8:]}"
        tx_id = str(entry.get("tx_id") or "unknown")
        short_tx = tx_id if len(tx_id) <= 18 else f"{tx_id[:10]}...{tx_id[-6:]}"
        amount = "unknown" if entry.get("amount_sompi") is None else format_kas(entry.get("amount_sompi"))
        lines.append(
            f"- {entry.get('direction', 'unknown')}: amount={amount} "
            f"fee={format_kas(entry.get('fee_sompi'))} tx={short_tx} {short_address}"
        )
    if events:
        lines.append("events:")
    for event in reversed(events[-8:]):
        address = str(event.get("address") or "")
        short_address = address if len(address) <= 24 else f"{address[:12]}...{address[-8:]}"
        label = event.get("label") or "unlabeled"
        lines.append(
            f"- {event.get('observed_at', 'unknown')} {event.get('direction', 'unknown')} "
            f"{label} delta={format_kas(event.get('delta_sompi'))} {short_address}"
        )
    if not pending_entries and not events:
        lines.append("no pending txs or recorded wallet events")
    return "\n".join(lines)


def format_discord_mining(report: dict[str, Any]) -> str:
    mining = report.get("mining") or {}
    lines = [
        f"Kaspa mining: {report.get('node_name', 'unknown')}",
        (
            f"enabled={mining.get('enabled', False)} "
            f"configured={mining.get('configured', False)} "
            f"ok={mining.get('ok', False)} "
            f"running={mining.get('running', False)} "
            f"mode={mining.get('mode', 'unknown')}"
        ),
        (
            f"hashrate={format_hashrate_local(mining.get('hashrate_hs'))} "
            f"accepted={mining.get('accepted_shares', 0)} "
            f"rejected={mining.get('rejected_shares', 0)}"
        ),
        f"last_share_at={mining.get('last_share_at') or 'unknown'}",
        (
            f"pool={mining.get('pool_url') or 'none'} "
            f"worker={mining.get('worker_name') or 'none'} "
            f"address_source={mining.get('wallet_address_source') or 'none'}"
        ),
        f"wallet_address={mining.get('wallet_address') or 'missing'}",
        f"detail={mining.get('detail', 'unknown')}",
    ]
    for process in (mining.get("processes") or [])[:5]:
        lines.append(f"- {process}")
    return "\n".join(lines)


def format_discord_whales(report: dict[str, Any]) -> str:
    whale = report.get("whale_watch") or {}
    events = list(whale.get("events") or [])
    summary = whale_watch_summary(events)
    lines = [
        f"Kaspa whales: {report.get('node_name', 'unknown')}",
        (
            f"enabled={whale.get('enabled', False)} "
            f"ok={whale.get('ok', False)} "
            f"threshold={format_kas(whale.get('min_amount_sompi'))} "
            f"mempool_entries={whale.get('mempool_entries', 0)} "
            f"candidates={len(whale.get('candidates') or [])}"
        ),
        (
            f"24h_count={summary.get('count_24h', 0)} "
            f"24h_volume={format_kas(summary.get('volume_24h_sompi'))} "
            f"total_events={summary.get('total_events', 0)}"
        ),
        f"detail={whale.get('detail', 'unknown')}",
    ]
    for event in reversed(events[-8:]):
        tx_id = str(event.get("tx_id") or "unknown")
        short_tx = tx_id if len(tx_id) <= 18 else f"{tx_id[:10]}...{tx_id[-6:]}"
        line = (
            f"- {event.get('observed_at', 'unknown')} "
            f"type={event.get('type', 'unknown')} "
            f"amount={format_kas(event.get('amount_sompi'))} "
            f"tx={short_tx} source={event.get('source', 'unknown')} status={event.get('status', 'new')}"
        )
        if event.get("tx_url"):
            line += f" link={event.get('tx_url')}"
        lines.append(line)
    if not events:
        lines.append("no whale events recorded")
    return "\n".join(lines)


def format_whale_daily_report(report: dict[str, Any]) -> str:
    whale = report.get("whale_watch") or {}
    events = list(whale.get("events") or [])
    summary = whale_watch_summary(events)
    lines = [
        f"enabled={whale.get('enabled', False)} ok={whale.get('ok', False)} threshold={format_kas(whale.get('min_amount_sompi'))}",
        (
            f"24h_count={summary.get('count_24h', 0)} "
            f"pending_24h={summary.get('pending_24h', 0)} "
            f"confirmed_24h={summary.get('confirmed_24h', 0)} "
            f"24h_volume={format_kas(summary.get('volume_24h_sompi'))}"
        ),
        (
            f"total_events={summary.get('total_events', 0)} "
            f"latest_amount={format_kas(summary.get('latest_amount_sompi'))} "
            f"latest_tx={short_hash(summary.get('latest_tx_id'))} "
            f"latest_observed={summary.get('latest_observed_at') or 'none'}"
        ),
        f"mempool_entries={whale.get('mempool_entries', 0)} candidates={len(whale.get('candidates') or [])} confirmed_candidates={len(whale.get('confirmed_candidates') or [])}",
        f"detail={whale.get('detail', 'unknown')} confirmed_detail={whale.get('confirmed_detail', 'unknown')}",
        f"explorer={whale.get('explorer_base_url') or 'off'}",
    ]
    if events:
        lines.append("recent_events:")
        for event in reversed(events[-5:]):
            tx_id = str(event.get("tx_id") or "unknown")
            short_tx = tx_id if len(tx_id) <= 18 else f"{tx_id[:10]}...{tx_id[-6:]}"
            line = (
                f"- {event.get('observed_at', 'unknown')} "
                f"type={event.get('type', 'unknown')} "
                f"source={event.get('source', 'unknown')} "
                f"status={event.get('status', 'new')} "
                f"amount={format_kas(event.get('amount_sompi'))} "
                f"tx={short_tx}"
            )
            if event.get("tx_url"):
                line += f" link={event.get('tx_url')}"
            lines.append(line)
    else:
        lines.append("recent_events=none")
    return "\n".join(lines)


def format_discord_incidents(
    report: dict[str, Any],
    state: dict[str, Any],
    recovery_records: list[dict[str, Any]] | None = None,
) -> str:
    incident = report.get("incident") or {}
    current = state.get("current_incident") or {}
    last = state.get("last_incident") or {}
    latest_recovery = (recovery_records or [])[-1] if recovery_records else {}
    active = bool(incident.get("active"))
    duration = numeric(incident.get("duration_seconds"))
    duration_text = "inactive" if duration is None else f"{duration / 60:.1f}m"
    failed = incident.get("failed_checks") or failed_check_names(report)
    causes = incident.get("causes") or check_failure_causes(report)
    return "\n".join(
        [
            f"Kaspa incidents: {report.get('node_name', 'unknown')}",
            f"current_active={active} duration={duration_text}",
            f"current_started_at={incident.get('started_at') or current.get('started_at') or 'none'}",
            f"current_failed_checks={','.join(failed) if failed else 'none'}",
            f"current_causes={', '.join(causes) if causes else 'none'}",
            f"last_resolved_at={last.get('resolved_at', 'none')}",
            (
                "latest_recovery="
                f"action={latest_recovery.get('action', 'none')} "
                f"operator_required={latest_recovery.get('operator_required', 'unknown')} "
                f"reason={latest_recovery.get('operator_reason') or latest_recovery.get('reason') or 'none'}"
            ),
        ]
    )


def format_operator_incident_summary(
    report: dict[str, Any],
    state: dict[str, Any],
    recovery_records: list[dict[str, Any]] | None = None,
) -> str:
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    last = state.get("last_incident") or {}
    latest_recovery = (recovery_records or [])[-1] if recovery_records else {}
    duration = numeric(incident.get("duration_seconds"))
    duration_text = "inactive" if duration is None else f"{duration / 60:.1f}m"
    failed = incident.get("failed_checks") or failed_check_names(report)
    causes = incident.get("causes") or report.get("failure_causes") or check_failure_causes(report)
    maintenance_until = (maintenance.get("mute_until") or "manual") if maintenance.get("active") else "none"
    return "\n".join(
        [
            f"health_score={report.get('health_score', 'unknown')}",
            f"incident_active={bool(incident.get('active'))}",
            f"incident_started_at={incident.get('started_at') or 'none'}",
            f"incident_duration={duration_text}",
            f"incident_failed_checks={','.join(failed) if failed else 'none'}",
            f"incident_causes={', '.join(causes) if causes else 'none'}",
            f"last_incident_resolved_at={last.get('resolved_at', 'none')}",
            f"maintenance_active={bool(maintenance.get('active'))}",
            f"maintenance_critical_only={maintenance.get('critical_only', True)}",
            f"maintenance_until={maintenance_until}",
            f"maintenance_reason={maintenance.get('reason') or 'none'}",
            (
                "latest_recovery="
                f"action={latest_recovery.get('action', 'none')} "
                f"before={latest_recovery.get('severity_before', 'unknown')} "
                f"after={latest_recovery.get('severity_after', 'unknown')} "
                f"reason={latest_recovery.get('operator_reason') or latest_recovery.get('reason') or 'none'}"
            ),
        ]
    )


def discord_command(
    config: dict,
    command: str,
    *,
    config_path: Path | None = None,
    mute_minutes: float = 30,
    reason: str = "",
    query_value: str = "",
    tx_id: str = "",
    amount_kas: Any = 0,
) -> int:
    if command in {"mute", "mute-all", "unmute"}:
        if config_path is None:
            print("discord command failed: mute/unmute requires -c/--config")
            return 2
        try:
            status = update_maintenance_config(
                config_path,
                mute_for_minutes=mute_minutes if command in {"mute", "mute-all"} else None,
                unmute=command == "unmute",
                critical_only=command != "mute-all",
                reason=reason,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"discord command failed: {exc}")
            return 2
        state = "active" if status["active"] else "off"
        until = (status["mute_until"] or "manual") if status["active"] else "none"
        print(
            "Kaspa maintenance: "
            f"{state} critical_only={status['critical_only']} "
            f"until={until} reason={status['reason'] or 'none'}"
        )
        return 0

    if command in {"watch-list", "watch-check", "watch-drill", "watch-add", "watch-remove", "watch-test"}:
        if command == "watch-list":
            report, _state = build_stateful_report(config)
            print(format_indexer_watch_status(report))
            return 0
        if command == "watch-check":
            report, _state = build_stateful_report(config)
            print(format_watch_readiness(report))
            return 0 if watch_readiness_ok(report) else 1
        if command == "watch-drill":
            return indexer_watch_drill(config, query_value, reason, tx_id, amount_kas)
        if command == "watch-test":
            return indexer_watch_test(config, query_value, reason)
        if config_path is None:
            print("discord command failed: watch add/remove requires -c/--config")
            return 2
        try:
            watch = update_indexer_watch_config(
                config_path,
                add_address=query_value if command == "watch-add" else None,
                remove_address=query_value if command == "watch-remove" else None,
                label=reason,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"discord command failed: {exc}")
            return 2
        targets = normalize_watch_addresses(watch.get("watch_addresses"))
        action = "added" if command == "watch-add" else "removed"
        print(
            "Kaspa indexer watchlist: "
            f"{action} addresses={len(targets)} enabled={bool(watch.get('enabled', False))}"
        )
        return 0

    if command == "tx":
        return indexer_lookup(config, "tx", query_value)
    if command == "address":
        return indexer_lookup(config, "address", query_value)
    if command == "search":
        return indexer_lookup(config, "search", query_value)
    if command == "balance":
        return indexer_lookup(config, "balance", query_value)
    if command == "utxos":
        return indexer_lookup(config, "utxos", query_value)

    report, state = build_stateful_report(config)
    if command == "status":
        print(format_discord_status(report))
        # Discord query commands should report unhealthy node state in text,
        # not as a failed shell command that hides the response from handlers.
        return 0
    if command == "wallet":
        print(format_discord_wallet(report))
        return 0
    if command == "wallet-txs":
        print(format_discord_wallet_txs(report))
        return 0
    if command == "mining":
        print(format_discord_mining(report))
        return 0
    if command == "whales":
        print(format_discord_whales(report))
        return 0
    if command == "incidents":
        print(format_discord_incidents(report, state, recent_recovery_records(config)))
        return 0
    if command == "maintenance":
        print(f"Kaspa {format_maintenance_status(config)}")
        return 0
    print(f"discord command failed: unsupported command {command}")
    return 2


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


def bps_highway_snapshot(config: dict) -> int:
    path = Path(config.get("bps_highway_snapshot_path") or DEFAULT_CONFIG["bps_highway_snapshot_path"])
    log_path = Path(config.get("log_path") or "")
    snapshot: dict[str, Any] = {
        "checked_at": dt.datetime.now().astimezone().isoformat(),
        "log_path": str(log_path),
        "ok": False,
        "progress": {
            "latest_processed": None,
            "latest_processed_age_seconds": None,
        },
    }
    if not log_path.exists():
        snapshot["error"] = "log_path_missing"
    else:
        scan_bytes = int(
            config.get("bps_highway_log_scan_bytes")
            or min(
                int(config.get("log_scan_bytes") or DEFAULT_CONFIG["log_scan_bytes"]),
                DEFAULT_CONFIG["bps_highway_log_scan_bytes"],
            )
        )
        lines = tail_lines(log_path, scan_bytes)
        processed_stats = parse_processed_stats(lines)
        if processed_stats:
            latest = processed_stats[-1]
            now = dt.datetime.now(latest.timestamp.tzinfo)
            snapshot["ok"] = True
            snapshot["progress"] = {
                "latest_processed": {
                    "timestamp": latest.timestamp.isoformat(),
                    "blocks": latest.blocks,
                    "headers": latest.headers,
                    "seconds": latest.seconds,
                    "transactions": latest.transactions,
                    "blocks_per_second": None if latest.seconds <= 0 else latest.blocks / latest.seconds,
                    "headers_per_second": None if latest.seconds <= 0 else latest.headers / latest.seconds,
                    "transactions_per_second": None if latest.seconds <= 0 else latest.transactions / latest.seconds,
                },
                "latest_processed_age_seconds": round(
                    max(0.0, (now - latest.timestamp).total_seconds()),
                    1,
                ),
            }
        else:
            snapshot["error"] = "processed_stats_missing"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest_processed = snapshot["progress"].get("latest_processed") or {}
    print(
        "BPS highway snapshot: "
        f"path={path} "
        f"bps={latest_processed.get('blocks_per_second', 'unknown')} "
        f"tx_s={latest_processed.get('transactions_per_second', 'unknown')} "
        f"age={snapshot['progress'].get('latest_processed_age_seconds', 'unknown')}"
    )
    return 0 if snapshot.get("ok") else 1


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


def market_summary(timeout: float = 6.0) -> int:
    print(format_market_snapshot(fetch_market_snapshot(timeout=timeout)))
    return 0


def market_snapshot(config: dict, timeout: float = 6.0) -> int:
    path = Path(config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"])
    snapshot = fetch_market_snapshot(timeout=timeout)
    try:
        history = load_jsonl(path)
    except (OSError, json.JSONDecodeError):
        history = []
    item = market_snapshot_item(snapshot, history=history)
    append_jsonl(path, item)
    print(f"Market snapshot saved: {path}")
    print(format_market_snapshot(snapshot_from_market_item(item)))
    return 0


def market_risk_level(score: int) -> str:
    if score >= 4:
        return "critical"
    if score >= 2:
        return "warning"
    return "ok"


def market_risk_drill(config: dict, *, score: int = 4, reason: str = "market_risk_drill", direction: str = "mixed") -> int:
    path = Path(config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"])
    try:
        history = load_jsonl(path)
    except (OSError, json.JSONDecodeError):
        history = []
    base = next((item for item in reversed(history) if item.get("ok")), {})
    item = dict(base)
    risk_reasons = [part.strip() for part in reason.split(",") if part.strip()] or ["market_risk_drill"]
    level = market_risk_level(int(score))
    item.update(
        {
            "checked_at": dt.datetime.now().astimezone().isoformat(),
            "source": item.get("source") or "Bybit KAS/USDT",
            "ok": True,
            "error": None,
            "market_risk_score": int(score),
            "market_risk_level": level,
            "market_risk_level_value": {"ok": 0, "warning": 1, "critical": 2}.get(level, -1),
            "market_risk_direction": direction or "mixed",
            "market_risk_reasons": ",".join(risk_reasons),
            "market_risk_reason_count": len(risk_reasons),
        }
    )
    append_jsonl(path, item)

    report, state = build_stateful_report(config)
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    history_db_path = sqlite_history_path(config)
    status_page_path = Path(config.get("status_page_path") or DEFAULT_CONFIG["status_page_path"])
    stream_page_path = Path(config.get("stream_page_path") or DEFAULT_CONFIG["stream_page_path"])
    recovery_records = recent_recovery_records(config)
    write_status_page(status_page_path, report, state, benchmark_path, recovery_records, history_db_path, path)
    if config.get("canvas_status_page_path") or DEFAULT_CONFIG["canvas_status_page_path"]:
        write_status_page(
            Path(config.get("canvas_status_page_path") or DEFAULT_CONFIG["canvas_status_page_path"]),
            report,
            state,
            benchmark_path,
            recovery_records,
            history_db_path,
            path,
        )
    write_stream_page(stream_page_path, report, state, benchmark_path, path)
    if config.get("canvas_stream_page_path") or DEFAULT_CONFIG["canvas_stream_page_path"]:
        write_stream_page(
            Path(config.get("canvas_stream_page_path") or DEFAULT_CONFIG["canvas_stream_page_path"]),
            report,
            state,
            benchmark_path,
            path,
        )
    write_prometheus_metrics(
        Path(config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"]),
        report,
        build_benchmark_summary(benchmark_path, limit=48),
        build_recovery_summary(recovery_history_path(config)),
        build_market_metrics(path),
        build_multi_node_metrics(sqlite_history_path(config)),
    )
    print(f"Market risk drill saved: {path}")
    print(format_market_snapshot(snapshot_from_market_item(item)))
    return 0


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


def build_market_metrics(path: Path) -> dict[str, Any]:
    try:
        records = load_jsonl(path)
    except (OSError, json.JSONDecodeError):
        records = []
    successful = [item for item in records if item.get("ok")]
    latest = records[-1] if records else {}
    latest_successful = successful[-1] if successful else {}
    return {
        "snapshots": len(records),
        "successful_snapshots": len(successful),
        "last_ok": latest.get("ok"),
        "last_checked_at": latest.get("checked_at"),
        "source": latest_successful.get("source") or latest.get("source") or "unknown",
        "latest_successful": latest_successful,
    }


def build_multi_node_metrics(path: Path) -> dict[str, Any]:
    return load_multi_node_history_status(path)


def format_prometheus_metrics(
    report: dict[str, Any],
    benchmark_summary: dict[str, Any],
    recovery_summary: dict[str, Any] | None = None,
    market_metrics: dict[str, Any] | None = None,
    multi_node_metrics: dict[str, Any] | None = None,
) -> str:
    node_labels = {"node": report["node_name"]}
    grpc_metrics = report.get("grpc_metrics") or {}
    sdk_metrics = report.get("sdk_metrics") or {}
    progress = report.get("progress") or {}
    latest_processed = progress.get("latest_processed") or {}
    sync_progress = report.get("sync_progress") or {}
    monitoring = report.get("monitoring") or {}
    disk = report.get("disk") or {}
    wallet = report.get("wallet") or {}
    mining_status = report.get("mining") or {}
    whale = report.get("whale_watch") or {}
    indexer = report.get("indexer") or {}
    indexer_metrics = indexer.get("metrics") or {}
    indexer_watch = report.get("indexer_watch") or {}
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    recovery_summary = recovery_summary or {}
    market_metrics = market_metrics or {}
    multi_node_metrics = multi_node_metrics or {}
    latest_market = market_metrics.get("latest_successful") or {}
    market_labels = {**node_labels, "source": market_metrics.get("source", "unknown")}
    severity_values = {"ok": 0, "warn": 1, "critical": 2}
    lines = [
        "# TYPE kaspa_watchtower_status_ok gauge",
        "# TYPE kaspa_watchtower_severity_value gauge",
        "# TYPE kaspa_watchtower_check_ok gauge",
        "# TYPE kaspa_watchtower_multi_node_available gauge",
        "# TYPE kaspa_watchtower_multi_node_verdict_value gauge",
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
    add_prometheus_metric(lines, "kaspa_watchtower_health_score", report.get("health_score"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_incident_active", bool(incident.get("active")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_incident_duration_seconds", incident.get("duration_seconds") or 0, node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_maintenance_active", bool(maintenance.get("active")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_mining_enabled", bool(mining_status.get("enabled")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_mining_ok", bool(mining_status.get("ok")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_mining_running", bool(mining_status.get("running")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_mining_hashrate_hs", mining_status.get("hashrate_hs"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_mining_accepted_shares", mining_status.get("accepted_shares"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_mining_rejected_shares", mining_status.get("rejected_shares"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_mining_last_share_age_seconds", mining_status.get("last_share_age_seconds"), node_labels)
    whale_summary = whale_watch_summary(list(whale.get("events") or []))
    add_prometheus_metric(lines, "kaspa_watchtower_whale_watch_enabled", bool(whale.get("enabled")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_watch_ok", bool(whale.get("ok")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_threshold_kas", whale.get("min_amount_kas"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_mempool_entries", whale.get("mempool_entries"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_candidates", len(whale.get("candidates") or []), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_confirmed_candidates", len(whale.get("confirmed_candidates") or []), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_confirmed_scan_enabled", bool(whale.get("confirmed_enabled")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_confirmed_baseline_available", bool(whale.get("confirmed_start_hash")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_events_total", whale_summary.get("total_events"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_latest_amount_kas", whale_summary.get("latest_amount_kas"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_24h_count", whale_summary.get("count_24h"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_whale_24h_volume_kas", whale_summary.get("volume_24h_kas"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_enabled", bool(indexer.get("enabled")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_ok", bool(indexer.get("ok")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_health_ok", bool(indexer.get("health_ok")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_syncing", bool(indexer.get("syncing")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_metrics_ok", bool(indexer.get("metrics_ok")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_lag_seconds", indexer_metrics.get("lag_seconds"), node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_indexer_checkpoint_age_seconds",
        indexer_metrics.get("checkpoint_age_seconds"),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_health_latency_ms", indexer.get("health_latency_ms"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_metrics_latency_ms", indexer.get("metrics_latency_ms"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_watch_readiness_ok", watch_readiness_ok(report), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_watch_enabled", bool(indexer_watch.get("enabled")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_watch_ok", bool(indexer_watch.get("ok")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_watch_addresses", len(indexer_watch.get("watch_addresses") or []), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_watch_events_total", len(indexer_watch.get("events") or []), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_indexer_watch_new_events", len(indexer_watch.get("new_events") or []), node_labels)
    for address_state in indexer_watch.get("address_states") or []:
        address_labels = {
            **node_labels,
            "address": address_state.get("address", "unknown"),
            "label": address_state.get("label") or "unlabeled",
        }
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_indexer_watch_address_ready",
            bool(address_state.get("ok")),
            address_labels,
        )
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_indexer_watch_address_balance_sompi",
            address_state.get("balance_sompi"),
            address_labels,
        )
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_indexer_watch_address_balance_kas",
            address_state.get("balance_kas"),
            address_labels,
        )
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_indexer_watch_address_utxos",
            address_state.get("utxo_count"),
            address_labels,
        )
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_indexer_watch_address_transactions",
            address_state.get("tx_count"),
            address_labels,
        )
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_indexer_watch_address_last_check_timestamp_seconds",
            iso_timestamp_seconds(address_state.get("last_checked_at")),
            address_labels,
        )
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_enabled", bool(wallet.get("enabled")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_ok", bool(wallet.get("ok")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_watch_addresses", len(wallet.get("entries") or []), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_balance_sompi", wallet.get("total_sompi"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_balance_kas", wallet.get("total_kas"), node_labels)
    wallet_change = wallet.get("change") or {}
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_changed", bool(wallet_change.get("changed")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_balance_delta_sompi", wallet_change.get("total_delta_sompi"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_balance_delta_kas", wallet_change.get("total_delta_kas"), node_labels)
    for entry in wallet.get("entries") or []:
        wallet_labels = {
            **node_labels,
            "address": entry.get("address", "unknown"),
            "label": entry.get("label") or "unlabeled",
        }
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_wallet_address_balance_sompi",
            entry.get("balance_sompi"),
            wallet_labels,
        )
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_wallet_address_balance_kas",
            entry.get("balance_kas"),
            wallet_labels,
        )
    mining = mining_reward_summary(
        list(wallet.get("events") or []),
        price_usdt=latest_market.get("spot_last_price"),
    )
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_rewards_total", mining.get("candidate_events"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_today_kas", mining.get("today_kas"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_7d_kas", mining.get("seven_day_kas"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_30d_kas", mining.get("thirty_day_kas"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_projected_monthly_kas", mining.get("projected_monthly_kas"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_latest_reward_age_hours", mining.get("latest_reward_age_hours"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_today_usd", mining.get("today_usd"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_7d_usd", mining.get("seven_day_usd"), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_wallet_mining_projected_monthly_usd", mining.get("projected_monthly_usd"), node_labels)
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
    sync_active = bool(sync_progress.get("active"))
    sync_baseline_available = bool(sync_progress.get("baseline_available")) if sync_active else False
    add_prometheus_metric(lines, "kaspa_watchtower_sync_active", sync_active, node_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_baseline_available",
        sync_baseline_available,
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_elapsed_minutes",
        sync_progress.get("elapsed_minutes", 0 if not sync_active else None),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_daa_delta",
        sync_progress.get("daa_delta", 0 if not sync_active else None),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_block_delta",
        sync_progress.get("block_delta", 0 if not sync_active else None),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_header_delta",
        sync_progress.get("header_delta", 0 if not sync_active else None),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_daa_rate_per_hour",
        sync_progress.get("daa_rate_per_hour", 0 if not sync_active else None),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_block_rate_per_hour",
        sync_progress.get("block_rate_per_hour", 0 if not sync_active else None),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sync_header_rate_per_hour",
        sync_progress.get("header_rate_per_hour", 0 if not sync_active else None),
        node_labels,
    )
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
    sdk_labels = {
        **node_labels,
        "endpoint": sdk_metrics.get("endpoint") or "unknown",
        "network": sdk_metrics.get("network_id") or "unknown",
        "encoding": sdk_metrics.get("encoding") or "unknown",
    }
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_enabled", bool(sdk_metrics.get("enabled")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_configured", bool(sdk_metrics.get("configured")), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_installed", bool(sdk_metrics.get("sdk_installed")), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_rpc_up", bool(sdk_metrics.get("ok")), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_connected", bool(sdk_metrics.get("is_connected")), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_connect_latency_ms", sdk_metrics.get("connect_latency_ms"), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_rpc_latency_ms", sdk_metrics.get("rpc_latency_ms"), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_peer_count", sdk_metrics.get("peer_count"), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_synced", sdk_metrics.get("is_synced"), sdk_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_virtual_daa_score",
        sdk_metrics.get("virtual_daa_score"),
        sdk_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_block_count", sdk_metrics.get("block_count"), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_header_count", sdk_metrics.get("header_count"), sdk_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_tip_count", sdk_metrics.get("tip_count"), sdk_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_enabled",
        bool(sdk_metrics.get("subscription_enabled")),
        sdk_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_sdk_subscription_ok", bool(sdk_metrics.get("subscription_ok")), sdk_labels)
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_duration_seconds",
        sdk_metrics.get("subscription_duration_seconds"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_events_total",
        sdk_metrics.get("subscription_events_total"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_last_event_age_seconds",
        sdk_metrics.get("subscription_last_event_age_seconds"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_block_added_total",
        sdk_metrics.get("subscription_block_added_total"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_virtual_chain_changed_total",
        sdk_metrics.get("subscription_virtual_chain_changed_total"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_virtual_daa_score_changed_total",
        sdk_metrics.get("subscription_virtual_daa_score_changed_total"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_last_virtual_daa_score",
        sdk_metrics.get("subscription_last_virtual_daa_score"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_watch_addresses",
        sdk_metrics.get("subscription_watch_addresses"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_utxos_changed_total",
        sdk_metrics.get("subscription_utxos_changed_total"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_utxos_added",
        sdk_metrics.get("subscription_utxos_added"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_utxos_removed",
        sdk_metrics.get("subscription_utxos_removed"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_utxo_added_sompi",
        sdk_metrics.get("subscription_utxo_added_sompi"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_subscription_utxo_removed_sompi",
        sdk_metrics.get("subscription_utxo_removed_sompi"),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_event_history_total",
        len(sdk_metrics.get("events") or []),
        sdk_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_sdk_new_events",
        len(sdk_metrics.get("new_events") or []),
        sdk_labels,
    )
    indexer_addresses = {
        str(item.get("address") or "")
        for item in normalize_watch_addresses(indexer_watch.get("watch_addresses"))
        if item.get("address")
    }
    sdk_addresses = {
        str(item.get("address") or "")
        for item in normalize_watch_addresses(sdk_metrics.get("subscription_watch_targets"))
        if item.get("address")
    }
    watch_source_specs = [
        ("indexer", len(indexer_addresses), len(indexer_watch.get("events") or []), len(indexer_watch.get("new_events") or [])),
        ("sdk", len(sdk_addresses), len(sdk_metrics.get("events") or []), len(sdk_metrics.get("new_events") or [])),
        ("both", len(indexer_addresses & sdk_addresses), 0, 0),
    ]
    for source, address_count, event_count, new_count in watch_source_specs:
        source_labels = {**node_labels, "source": source}
        add_prometheus_metric(lines, "kaspa_watchtower_watch_source_addresses", address_count, source_labels)
        add_prometheus_metric(lines, "kaspa_watchtower_watch_source_events_total", event_count, source_labels)
        add_prometheus_metric(lines, "kaspa_watchtower_watch_source_new_events", new_count, source_labels)
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
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_snapshots_total",
        market_metrics.get("snapshots"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_successful_snapshots_total",
        market_metrics.get("successful_snapshots"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_snapshot_ok",
        market_metrics.get("last_ok"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_snapshot_timestamp_seconds",
        iso_timestamp_seconds(market_metrics.get("last_checked_at")),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_price_usdt",
        latest_market.get("spot_last_price"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_change_24h_ratio",
        latest_market.get("spot_change_24h"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_volume_24h_kas",
        latest_market.get("spot_volume_24h"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_price_median_usdt",
        latest_market.get("spot_price_median"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_price_min_usdt",
        latest_market.get("spot_price_min"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_price_max_usdt",
        latest_market.get("spot_price_max"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_price_dispersion_percent",
        latest_market.get("spot_price_dispersion_pct"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_price_sources",
        latest_market.get("spot_price_sources"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_spot_price_source_errors",
        latest_market.get("spot_price_source_errors"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_basis_percent",
        latest_market.get("futures_basis_pct"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_funding_rate",
        latest_market.get("futures_funding_rate"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_funding_apr_percent",
        latest_market.get("futures_funding_apr_pct"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_funding_z_score",
        latest_market.get("futures_funding_z_score"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_open_interest_kas",
        latest_market.get("futures_open_interest"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_open_interest_value_usdt",
        latest_market.get("futures_open_interest_value"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_volume_24h_kas",
        latest_market.get("futures_volume_24h"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_futures_oi_volume_ratio",
        latest_market.get("futures_oi_volume_ratio"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_positioning_risk_score",
        latest_market.get("market_risk_score"),
        market_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_positioning_risk_level",
        latest_market.get("market_risk_level_value"),
        {**market_labels, "level": latest_market.get("market_risk_level", "unknown")},
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_market_positioning_risk_reasons",
        latest_market.get("market_risk_reason_count"),
        {
            **market_labels,
            "direction": latest_market.get("market_risk_direction", "unknown"),
            "reasons": latest_market.get("market_risk_reasons", "none"),
        },
    )
    verdict_values = {"ok": 0, "warn": 1, "critical": 2, "unknown": -1}
    multi_node_nodes = multi_node_metrics.get("nodes") or []
    risk_nodes = [item for item in multi_node_nodes if item.get("flags")]
    lagging_nodes = [
        item
        for item in multi_node_nodes
        if "daa_lag" in (item.get("flags") or []) or "block_lag" in (item.get("flags") or [])
    ]
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_multi_node_available",
        bool(multi_node_metrics.get("available")),
        node_labels,
    )
    add_prometheus_metric(
        lines,
        "kaspa_watchtower_multi_node_verdict_value",
        verdict_values.get(str(multi_node_metrics.get("verdict", "unknown")), -1),
        node_labels,
    )
    add_prometheus_metric(lines, "kaspa_watchtower_multi_node_nodes", len(multi_node_nodes), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_multi_node_risk_nodes", len(risk_nodes), node_labels)
    add_prometheus_metric(lines, "kaspa_watchtower_multi_node_lagging_nodes", len(lagging_nodes), node_labels)
    for item in multi_node_nodes:
        history_labels = {
            **node_labels,
            "history_node": item.get("node_name", "unknown"),
            "network": item.get("network", "unknown"),
        }
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_multi_node_node_severity_value",
            severity_values.get(str(item.get("latest_severity")), -1),
            history_labels,
        )
        add_prometheus_metric(lines, "kaspa_watchtower_multi_node_node_ok_ratio", item.get("ok_ratio"), history_labels)
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_multi_node_check_lag_minutes",
            item.get("check_lag_minutes"),
            history_labels,
        )
        add_prometheus_metric(lines, "kaspa_watchtower_multi_node_daa_lag", item.get("daa_lag"), history_labels)
        add_prometheus_metric(lines, "kaspa_watchtower_multi_node_block_lag", item.get("block_lag"), history_labels)
        add_prometheus_metric(lines, "kaspa_watchtower_multi_node_peer_lag", item.get("peer_lag"), history_labels)
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_multi_node_processed_age_lag_seconds",
            item.get("processed_age_lag_seconds"),
            history_labels,
        )
        add_prometheus_metric(
            lines,
            "kaspa_watchtower_multi_node_flag_count",
            len(item.get("flags") or []),
            history_labels,
        )
        for flag in item.get("flags") or []:
            add_prometheus_metric(
                lines,
                "kaspa_watchtower_multi_node_flag",
                1,
                {**history_labels, "flag": flag},
            )
    return "\n".join(lines) + "\n"


def write_prometheus_metrics(
    path: Path,
    report: dict[str, Any],
    benchmark_summary: dict[str, Any],
    recovery_summary: dict[str, Any] | None = None,
    market_metrics: dict[str, Any] | None = None,
    multi_node_metrics: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        format_prometheus_metrics(report, benchmark_summary, recovery_summary, market_metrics, multi_node_metrics),
        encoding="utf-8",
    )


def prometheus(config: dict) -> int:
    report, _state = build_stateful_report(config)
    benchmark_path = Path(config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"])
    metrics_path = Path(config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"])
    benchmark_summary = build_benchmark_summary(benchmark_path, limit=48)
    recovery_summary = build_recovery_summary(recovery_history_path(config))
    market_metrics = build_market_metrics(Path(config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"]))
    multi_node_metrics = build_multi_node_metrics(sqlite_history_path(config))
    write_prometheus_metrics(metrics_path, report, benchmark_summary, recovery_summary, market_metrics, multi_node_metrics)
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


def path_suffix_check(name: str, value: Any, suffixes: Iterable[str]) -> Check:
    text = str(value or "")
    allowed = tuple(suffixes)
    ok = bool(text) and Path(text).suffix in allowed
    return Check(name, ok, validation_detail(text, "path ending in " + " or ".join(allowed), ok))


def json_file_check(name: str, value: Any) -> Check:
    text = str(value or "")
    ok = False
    detail = validation_detail(text, "valid JSON file", ok)
    if text and Path(text).exists():
        try:
            json.loads(Path(text).read_text(encoding="utf-8"))
            ok = True
            detail = validation_detail(text, "valid JSON file", ok)
        except json.JSONDecodeError as exc:
            detail = f"{text}; expected valid JSON file ({exc.msg})"
    return Check(name, ok, detail)


def endpoint_check(name: str, value: Any) -> Check:
    text = str(value or "")
    ok = endpoint_configured(text)
    return Check(name, ok, validation_detail(text, "host:port endpoint", ok))


def env_threshold_check(name: str, expected: str) -> Check:
    value = os.environ.get(name)
    if value is None:
        return Check(f"env.{name}", True, "unset; ok")
    if expected.startswith("integer"):
        validator = non_negative_int_config
    elif expected == "number >= 1":
        validator = lambda item: number_between_config(item, 1)
    else:
        validator = lambda item: number_between_config(item, 0)
    ok = validator(value)
    return Check(f"env.{name}", ok, validation_detail(value, expected, ok))


def config_validation_checks(config: dict) -> list[Check]:
    recovery = config.get("recovery") or {}
    if not isinstance(recovery, dict):
        recovery = {}
    restart_command = recovery.get("restart_command") or []
    recovery_mode = recovery.get("mode", DEFAULT_CONFIG["recovery"]["mode"])
    recovery_policy = recovery_policy_config(config)
    config_version = config.get("config_version", DEFAULT_CONFIG["config_version"])
    config_version_ok = isinstance(config_version, int) and 1 <= config_version <= DEFAULT_CONFIG["config_version"]
    node_name = str(config.get("node_name") or "")
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
            bool(node_name),
            validation_detail(node_name, "non-empty node name", bool(node_name)),
        ),
        Check(
            "node_name.format",
            bool(NODE_NAME_PATTERN.fullmatch(node_name)),
            validation_detail(
                node_name,
                "lowercase slug using a-z, 0-9, dot, dash, or underscore",
                bool(NODE_NAME_PATTERN.fullmatch(node_name)),
            ),
        ),
        Check(
            "node_name.network_hint",
            bool(NODE_NETWORK_HINT_PATTERN.search(node_name)),
            validation_detail(
                node_name,
                "node name containing network hint such as mainnet, testnet, tn10, simnet, or devnet",
                bool(NODE_NETWORK_HINT_PATTERN.search(node_name)),
            ),
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
        parent_writable_check(
            "stream_page_path",
            config.get("stream_page_path") or DEFAULT_CONFIG["stream_page_path"],
        ),
        parent_writable_check("benchmark_path", config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"]),
        parent_writable_check(
            "sqlite_history_path",
            config.get("sqlite_history_path") or DEFAULT_CONFIG["sqlite_history_path"],
        ),
        path_suffix_check(
            "sqlite_history_path.suffix",
            config.get("sqlite_history_path") or DEFAULT_CONFIG["sqlite_history_path"],
            (".sqlite", ".db"),
        ),
        parent_writable_check(
            "market_snapshot_path",
            config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"],
        ),
        parent_writable_check(
            "prometheus_metrics_path",
            config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"],
        ),
        path_suffix_check(
            "prometheus_metrics_path.suffix",
            config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"],
            (".prom",),
        ),
        parent_writable_check(
            "recovery_history_path",
            config.get("recovery_history_path") or DEFAULT_CONFIG["recovery_history_path"],
        ),
        json_file_check("grafana.dashboard_json", "grafana/kaspa-watchtower.json"),
        path_exists_check("prometheus.rules_file", "prometheus/kaspa-watchtower-rules.yml"),
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
    recovery_policy_specs = [
        ("require_critical", lambda value: isinstance(value, bool), "boolean"),
        ("min_consecutive_failures", positive_int_config, "integer > 0"),
        ("min_incident_minutes", lambda value: number_between_config(value, 0), "number >= 0"),
        ("require_same_failed_checks", lambda value: isinstance(value, bool), "boolean"),
        ("allow_during_maintenance", lambda value: isinstance(value, bool), "boolean"),
    ]
    for key, validator, expected in recovery_policy_specs:
        value = recovery_policy.get(key)
        ok = validator(value)
        checks.append(Check(f"recovery.policy.{key}", ok, validation_detail(value, expected, ok)))
    threshold_specs = [
        ("alert_repeat_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("stale_log_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("stale_processed_stats_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("progress_window_minutes", lambda value: number_between_config(value, 1), "number >= 1"),
        ("min_relay_blocks_in_window", non_negative_int_config, "integer >= 0"),
        ("min_peer_count", non_negative_int_config, "integer >= 0"),
        ("min_active_peer_count", non_negative_int_config, "integer >= 0"),
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
    maintenance_specs = [
        ("enabled", lambda value: isinstance(value, bool), "boolean"),
        ("mute_until", lambda value: value in (None, "") or parse_iso_datetime(str(value)) is not None, "ISO datetime or empty string"),
        ("critical_only", lambda value: isinstance(value, bool), "boolean"),
        ("reason", lambda value: value is None or isinstance(value, str), "string"),
    ]
    for key, validator, expected in maintenance_specs:
        value = nested_config_value(config, "maintenance", key)
        ok = validator(value)
        checks.append(Check(f"maintenance.{key}", ok, validation_detail(value, expected, ok)))

    wallet = config.get("wallet") if isinstance(config.get("wallet"), dict) else {}
    wallet_specs = [
        ("enabled", lambda value: isinstance(value, bool), "boolean"),
        ("alert_on_change", lambda value: isinstance(value, bool), "boolean"),
        ("alert_min_delta_sompi", non_negative_int_config, "integer >= 0"),
        ("alert_directions", lambda value: value in {"all", "incoming", "outgoing"} or isinstance(value, list), "all, incoming, outgoing, or list"),
        ("large_outgoing_alert_sompi", non_negative_int_config, "integer >= 0"),
        ("mining_reward_stale_hours", lambda value: number_between_config(value, 0), "number >= 0"),
        ("event_history_entries", positive_int_config, "integer > 0"),
    ]
    for key, validator, expected in wallet_specs:
        value = wallet.get(key, DEFAULT_CONFIG["wallet"].get(key))
        ok = validator(value)
        checks.append(Check(f"wallet.{key}", ok, validation_detail(value, expected, ok)))
    watch_addresses = wallet.get("watch_addresses", [])
    watch_addresses_ok = isinstance(watch_addresses, list) and all(
        isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("address"), str))
        for item in watch_addresses
    )
    checks.append(
        Check(
            "wallet.watch_addresses",
            watch_addresses_ok,
            validation_detail(watch_addresses, "list of address strings or {label,address} objects", watch_addresses_ok),
        )
    )
    mining = config.get("mining") if isinstance(config.get("mining"), dict) else {}
    mining_specs = [
        ("enabled", lambda value: isinstance(value, bool), "boolean"),
        ("mode", lambda value: value in {"disabled", "cpu-test", "external-miner", "macos-gpu-experimental"}, "disabled, cpu-test, external-miner, or macos-gpu-experimental"),
        ("process_match", lambda value: value is None or isinstance(value, str), "string"),
        ("log_path", lambda value: value is None or isinstance(value, str), "string"),
        ("pool_url", lambda value: value is None or isinstance(value, str), "string"),
        ("wallet_address", lambda value: value in (None, "") or looks_like_kaspa_address(value), "empty or Kaspa address"),
        ("worker_name", lambda value: value is None or isinstance(value, str), "string"),
        ("expected_hashrate_min_hs", lambda value: number_between_config(value, 0), "number >= 0"),
        ("stale_share_minutes", lambda value: number_between_config(value, 0), "number >= 0"),
    ]
    for key, validator, expected in mining_specs:
        value = mining.get(key, DEFAULT_CONFIG["mining"].get(key))
        ok = validator(value)
        checks.append(Check(f"mining.{key}", ok, validation_detail(value, expected, ok)))
    resolved_mining_address, resolved_mining_address_source = mining_wallet_address(config)
    mining_address_ready = (not bool(mining.get("enabled", DEFAULT_CONFIG["mining"]["enabled"]))) or looks_like_kaspa_address(resolved_mining_address)
    checks.append(
        Check(
            "mining.wallet_address_ready",
            mining_address_ready,
            validation_detail(
                f"{resolved_mining_address_source}:{'set' if resolved_mining_address else 'missing'}",
                "set mining.wallet_address or wallet.watch_addresses label=mining before enabling mining",
                mining_address_ready,
            ),
        )
    )
    whale = config.get("whale_watch") if isinstance(config.get("whale_watch"), dict) else {}
    whale_specs = [
        ("enabled", lambda value: isinstance(value, bool), "boolean"),
        ("confirmed_enabled", lambda value: isinstance(value, bool), "boolean"),
        ("min_amount_sompi", positive_int_config, "integer > 0"),
        ("alert_enabled", lambda value: isinstance(value, bool), "boolean"),
        ("event_history_entries", positive_int_config, "integer > 0"),
        ("explorer_base_url", lambda value: value in (None, "") or bool(re.fullmatch(r"https?://[^/\s]+.*", str(value))), "empty or http(s) URL"),
        ("explorer_tx_path", lambda value: isinstance(value, str) and "{tx_id}" in value, "string containing {tx_id}"),
        ("explorer_address_path", lambda value: isinstance(value, str) and "{address}" in value, "string containing {address}"),
    ]
    for key, validator, expected in whale_specs:
        value = whale.get(key, DEFAULT_CONFIG["whale_watch"].get(key))
        ok = validator(value)
        checks.append(Check(f"whale_watch.{key}", ok, validation_detail(value, expected, ok)))
    indexer = config.get("indexer") if isinstance(config.get("indexer"), dict) else {}
    indexer_specs = [
        ("enabled", lambda value: isinstance(value, bool), "boolean"),
        ("base_url", lambda value: value in (None, "") or bool(re.fullmatch(r"https?://[^/\s]+.*", str(value))), "empty or http(s) URL"),
        ("health_path", lambda value: isinstance(value, str) and bool(value.strip()), "non-empty path or URL"),
        ("metrics_path", lambda value: isinstance(value, str) and bool(value.strip()), "non-empty path or URL"),
        ("timeout_seconds", lambda value: number_between_config(value, 0.1), "number >= 0.1"),
        ("require_metrics", lambda value: isinstance(value, bool), "boolean"),
        ("max_lag_seconds", lambda value: number_between_config(value, 0), "number >= 0"),
        ("max_checkpoint_age_seconds", lambda value: number_between_config(value, 0), "number >= 0"),
        ("transaction_path", lambda value: isinstance(value, str) and "{tx_id}" in value, "string containing {tx_id}"),
        ("address_transactions_path", lambda value: isinstance(value, str) and "{address}" in value, "string containing {address}"),
        ("address_balance_path", lambda value: isinstance(value, str) and "{address}" in value, "string containing {address}"),
        ("address_utxos_path", lambda value: isinstance(value, str) and "{address}" in value, "string containing {address}"),
        ("search_path", lambda value: isinstance(value, str) and "{query}" in value, "string containing {query}"),
    ]
    for key, validator, expected in indexer_specs:
        value = indexer.get(key, DEFAULT_CONFIG["indexer"].get(key))
        ok = validator(value)
        checks.append(Check(f"indexer.{key}", ok, validation_detail(value, expected, ok)))
    indexer_watch = config.get("indexer_watch") if isinstance(config.get("indexer_watch"), dict) else {}
    indexer_watch_specs = [
        ("enabled", lambda value: isinstance(value, bool), "boolean"),
        ("alert_enabled", lambda value: isinstance(value, bool), "boolean"),
        ("event_history_entries", positive_int_config, "integer > 0"),
    ]
    for key, validator, expected in indexer_watch_specs:
        value = indexer_watch.get(key, DEFAULT_CONFIG["indexer_watch"].get(key))
        ok = validator(value)
        checks.append(Check(f"indexer_watch.{key}", ok, validation_detail(value, expected, ok)))
    indexer_watch_addresses = indexer_watch.get("watch_addresses", DEFAULT_CONFIG["indexer_watch"]["watch_addresses"])
    indexer_watch_addresses_ok = isinstance(indexer_watch_addresses, list) and all(
        isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("address"), str))
        for item in indexer_watch_addresses
    )
    checks.append(
        Check(
            "indexer_watch.watch_addresses",
            indexer_watch_addresses_ok,
            validation_detail(indexer_watch_addresses, "list of address strings or {label,address} objects", indexer_watch_addresses_ok),
        )
    )
    history_paths = {
        "state_path": config.get("state_path") or DEFAULT_CONFIG["state_path"],
        "benchmark_path": config.get("benchmark_path") or DEFAULT_CONFIG["benchmark_path"],
        "sqlite_history_path": config.get("sqlite_history_path") or DEFAULT_CONFIG["sqlite_history_path"],
        "market_snapshot_path": config.get("market_snapshot_path") or DEFAULT_CONFIG["market_snapshot_path"],
        "prometheus_metrics_path": config.get("prometheus_metrics_path") or DEFAULT_CONFIG["prometheus_metrics_path"],
        "recovery_history_path": config.get("recovery_history_path") or DEFAULT_CONFIG["recovery_history_path"],
    }
    normalized_paths = [str(Path(value).expanduser()) for value in history_paths.values()]
    paths_distinct = len(normalized_paths) == len(set(normalized_paths))
    checks.append(
        Check(
            "history_paths.distinct",
            paths_distinct,
            validation_detail(
                ", ".join(f"{key}={value}" for key, value in history_paths.items()),
                "distinct state, benchmark, SQLite, market, Prometheus, and recovery paths",
                paths_distinct,
            ),
        )
    )
    for name, expected in MULTI_NODE_THRESHOLD_ENV_SPECS.items():
        checks.append(env_threshold_check(name, expected))
    canvas_status_page = config.get("canvas_status_page_path") or ""
    if canvas_status_page:
        checks.append(parent_writable_check("canvas_status_page_path", canvas_status_page))
    canvas_stream_page = config.get("canvas_stream_page_path") or ""
    if canvas_stream_page:
        checks.append(parent_writable_check("canvas_stream_page_path", canvas_stream_page))
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


def recovery_policy_config(config: dict) -> dict[str, Any]:
    recovery = config.get("recovery") or {}
    if not isinstance(recovery, dict):
        recovery = {}
    policy = dict(DEFAULT_CONFIG["recovery"]["policy"])
    configured = recovery.get("policy") or {}
    if isinstance(configured, dict):
        policy.update(configured)
    return policy


def recovery_policy_decision(
    report: dict[str, Any],
    state: dict[str, Any],
    config: dict,
    *,
    force: bool = False,
) -> dict[str, Any]:
    policy = recovery_policy_config(config)
    failed = failed_check_names(report)
    current_failed = set(failed)
    min_consecutive = positive_int(policy.get("min_consecutive_failures"), 3)
    min_incident_minutes = float(policy.get("min_incident_minutes") or 0)
    incident = report.get("incident") or {}
    maintenance = report.get("maintenance") or {}
    history = list(state.get("history") or [])
    history.append(history_item(report))

    consecutive = 0
    for item in reversed(history):
        if item.get("status") == "ok" or item.get("severity") == "ok":
            break
        item_failed = set(item.get("failed_checks") or [])
        if policy.get("require_same_failed_checks", True) and item_failed != current_failed:
            break
        consecutive += 1

    duration_seconds = numeric(incident.get("duration_seconds"))
    if duration_seconds is None:
        duration_seconds = 0.0

    base = {
        "force": force,
        "allowed": False,
        "reason": "",
        "policy": policy,
        "consecutive_failures": consecutive,
        "min_consecutive_failures": min_consecutive,
        "incident_duration_seconds": duration_seconds,
        "min_incident_seconds": min_incident_minutes * 60,
        "failed_checks": failed,
        "maintenance_active": bool(maintenance.get("active")),
    }

    if force:
        return {**base, "allowed": True, "reason": "force_recover"}
    if report.get("severity") == "ok":
        return {**base, "reason": "node_healthy"}
    if policy.get("require_critical", True) and report.get("severity") != "critical":
        return {**base, "reason": "severity_not_critical"}
    if maintenance.get("active") and not policy.get("allow_during_maintenance", False):
        return {**base, "reason": "maintenance_active"}
    if duration_seconds < min_incident_minutes * 60:
        return {**base, "reason": "incident_duration_below_policy"}
    if consecutive < min_consecutive:
        return {**base, "reason": "consecutive_failures_below_policy"}
    return {**base, "allowed": True, "reason": "policy_satisfied"}


def format_recovery_decision(
    report: dict[str, Any],
    *,
    mode: str,
    restart_command: list[str],
    force: bool,
    dry_run: bool,
    policy_decision: dict[str, Any] | None = None,
) -> str:
    failed = failed_check_names(report)
    failed_text = ",".join(failed) if failed else "none"
    configured = bool(restart_command)
    policy_decision = policy_decision or {"allowed": True, "reason": "not_evaluated"}
    if not configured:
        next_action = "configure recovery.restart_command before recovery"
    elif mode != "manual":
        next_action = f"unsupported recovery mode {mode}"
    elif not policy_decision.get("allowed"):
        next_action = f"skip recovery; policy blocked: {policy_decision.get('reason')}"
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
            (
                "  policy="
                f"allowed={policy_decision.get('allowed')} "
                f"reason={policy_decision.get('reason')} "
                f"consecutive={policy_decision.get('consecutive_failures', 'unknown')}/"
                f"{policy_decision.get('min_consecutive_failures', 'unknown')} "
                f"incident={float(policy_decision.get('incident_duration_seconds') or 0) / 60:.1f}m/"
                f"{float(policy_decision.get('min_incident_seconds') or 0) / 60:.1f}m "
                f"maintenance_active={policy_decision.get('maintenance_active', False)}"
            ),
            f"  next={next_action}",
        ]
    )


def recover(config: dict, *, force: bool = False, dry_run: bool = False) -> int:
    state_path = Path(config.get("state_path") or DEFAULT_CONFIG["state_path"])
    state = load_state(state_path)
    report = build_report(config)
    apply_stateful_checks(report, state, config)
    enrich_operational_fields(report, config, state)
    recovery = config.get("recovery", {})
    restart_command = recovery.get("restart_command") or []
    mode = recovery.get("mode", "manual")
    policy_decision = recovery_policy_decision(report, state, config, force=force)
    record: dict[str, Any] = {
        "started_at": dt.datetime.now().astimezone().isoformat(),
        "node_name": report.get("node_name"),
        "status_before": report.get("status"),
        "severity_before": report.get("severity"),
        "failed_checks_before": failed_check_names(report),
        "failed_check_details_before": failed_check_details(report),
        "mode": mode,
        "force": force,
        "dry_run": dry_run,
        "restart_command": restart_command,
        "policy": policy_decision,
    }
    print(
        format_recovery_decision(
            report,
            mode=mode,
            restart_command=restart_command,
            force=force,
            dry_run=dry_run,
            policy_decision=policy_decision,
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
    if not policy_decision.get("allowed"):
        record.update({"action": "skipped", "reason": f"policy:{policy_decision.get('reason')}"})
        print(f"Recovery skipped: policy blocked; reason={policy_decision.get('reason')}")
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
        record.update(
            {
                "completed_at": dt.datetime.now().astimezone().isoformat(),
                "operator_required": True,
                "operator_reason": "recovery_command_failed",
            }
        )
        print("Operator required: recovery command failed; manual intervention needed")
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
            "failed_check_details_after": failed_check_details(after),
        }
    )
    print(f"Post-recovery status: {after['status']} severity={after['severity']}")
    if after["status"] != "ok":
        record.update(
            {
                "operator_required": True,
                "operator_reason": "post_recovery_unhealthy",
            }
        )
        failed_after = ",".join(record["failed_checks_after"]) or "none"
        print(f"Operator required: post-recovery check still unhealthy; failed_checks={failed_after}")
    else:
        record["operator_required"] = False
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
    parser.add_argument("--stream-page", action="store_true", help="Write the 1080p OBS/YouTube rotating stream page.")
    parser.add_argument("--recover", action="store_true", help="Run the configured manual recovery command when unhealthy.")
    parser.add_argument("--force-recover", action="store_true", help="Run recovery even when the current report is healthy.")
    parser.add_argument("--dry-run", action="store_true", help="Show recovery command without executing it.")
    parser.add_argument("--benchmark-snapshot", action="store_true", help="Append a benchmark snapshot to the JSONL benchmark log.")
    parser.add_argument("--bps-highway-snapshot", action="store_true", help="Write the lightweight BPS highway JSON snapshot.")
    parser.add_argument("--benchmark-report", action="store_true", help="Print a benchmark report from saved snapshots.")
    parser.add_argument("--benchmark-limit", type=int, default=100, help="Number of recent benchmark snapshots to include.")
    parser.add_argument("--market-summary", action="store_true", help="Print an optional public KAS/USDT market snapshot.")
    parser.add_argument("--market-snapshot", action="store_true", help="Append a public KAS/USDT market snapshot to JSONL history.")
    parser.add_argument("--market-risk-drill", action="store_true", help="Inject a synthetic market positioning risk snapshot.")
    parser.add_argument("--market-risk-score", type=int, default=4, help="Synthetic score for --market-risk-drill.")
    parser.add_argument("--market-risk-reason", default="market_risk_drill", help="Comma-separated reasons for --market-risk-drill.")
    parser.add_argument("--market-risk-direction", default="mixed", help="Synthetic crowding direction for --market-risk-drill.")
    parser.add_argument("--market-timeout", type=float, default=6.0, help="Public market API timeout in seconds.")
    parser.add_argument("--indexer-tx", help="Query a transaction from the configured local indexer API.")
    parser.add_argument("--indexer-address", help="Query address transactions from the configured local indexer API.")
    parser.add_argument("--indexer-balance", help="Query address balance from the configured local indexer API.")
    parser.add_argument("--indexer-utxos", help="Query address UTXOs from the configured local indexer API.")
    parser.add_argument("--indexer-search", help="Search the configured local indexer API.")
    parser.add_argument("--indexer-watch-add", help="Add an address to the indexer watchlist.")
    parser.add_argument("--indexer-watch-remove", help="Remove an address or label from the indexer watchlist.")
    parser.add_argument("--indexer-watch-test", help="Test indexer reads for a watch address without changing config.")
    parser.add_argument("--indexer-watch-label", default="", help="Label for --indexer-watch-add.")
    parser.add_argument("--indexer-watch-list", action="store_true", help="Print the indexer watchlist.")
    parser.add_argument("--prometheus", action="store_true", help="Write Prometheus textfile metrics.")
    parser.add_argument("--validate-config", action="store_true", help="Validate config paths, endpoints, and commands.")
    parser.add_argument("--prune-state", action="store_true", help="Apply configured retention limits to local state files.")
    parser.add_argument("--maintenance-status", action="store_true", help="Print current maintenance mute state.")
    parser.add_argument("--mute-for", type=float, help="Mute non-critical alerts for this many minutes by updating config.")
    parser.add_argument("--maintenance-reason", default="", help="Reason stored with --mute-for.")
    parser.add_argument("--set-mining-address", help="Store the mining payout address in config.json.")
    parser.add_argument("--clear-mining-address", action="store_true", help="Clear the stored mining payout address.")
    parser.add_argument(
        "--discord-command",
        choices=(
            "status",
            "incidents",
            "maintenance",
            "wallet",
            "wallet-txs",
            "mining",
            "whales",
            "tx",
            "address",
            "search",
            "balance",
            "utxos",
            "watch-list",
            "watch-check",
            "watch-drill",
            "watch-add",
            "watch-remove",
            "watch-test",
            "mute",
            "mute-all",
            "unmute",
        ),
        help="Run a Discord-friendly watchtower command.",
    )
    parser.add_argument("--discord-mute-minutes", type=float, default=30, help="Mute window for --discord-command mute.")
    parser.add_argument("--discord-query", default="", help="Lookup value for --discord-command tx/address/search/balance/utxos.")
    parser.add_argument("--discord-tx-id", default="", help="Synthetic tx id for --discord-command watch-drill.")
    parser.add_argument("--discord-amount-kas", type=float, default=0.0, help="Synthetic amount for --discord-command watch-drill.")
    parser.add_argument(
        "--mute-all",
        action="store_true",
        help="Mute all alerts during --mute-for, including critical alerts.",
    )
    parser.add_argument("--unmute", action="store_true", help="Clear maintenance mute state in config.")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.set_mining_address or args.clear_mining_address:
        if args.config is None:
            parser.error("--set-mining-address and --clear-mining-address require -c/--config")
        if args.set_mining_address and args.clear_mining_address:
            parser.error("--set-mining-address and --clear-mining-address cannot be used together")
        try:
            mining_state = update_mining_address_config(
                args.config,
                address=args.set_mining_address,
                clear=args.clear_mining_address,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"mining address update failed: {exc}")
            return 2
        address_state = "cleared" if not mining_state.get("wallet_address") else "set"
        print(
            "mining_address="
            f"{address_state} "
            f"source=mining.wallet_address "
            f"worker={mining_state.get('worker_name') or 'none'}"
        )
        return 0
    if args.mute_for is not None or args.unmute:
        if args.config is None:
            parser.error("--mute-for and --unmute require -c/--config")
        if args.mute_for is not None and args.unmute:
            parser.error("--mute-for and --unmute cannot be used together")
        try:
            maintenance_state = update_maintenance_config(
                args.config,
                mute_for_minutes=args.mute_for,
                unmute=args.unmute,
                critical_only=not args.mute_all,
                reason=args.maintenance_reason,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"maintenance update failed: {exc}")
            return 2
        state = "active" if maintenance_state["active"] else "off"
        until = (maintenance_state["mute_until"] or "manual") if maintenance_state["active"] else "none"
        print(
            "maintenance="
            f"{state} "
            f"critical_only={maintenance_state['critical_only']} "
            f"until={until} "
            f"reason={maintenance_state['reason'] or 'none'}"
        )
        return 0
    if args.indexer_watch_test:
        return indexer_watch_test(config, args.indexer_watch_test, args.indexer_watch_label)
    if args.indexer_watch_list or args.indexer_watch_add or args.indexer_watch_remove:
        if args.config is None:
            parser.error("--indexer-watch-* requires -c/--config")
        if sum(bool(item) for item in (args.indexer_watch_list, args.indexer_watch_add, args.indexer_watch_remove)) != 1:
            parser.error("use exactly one of --indexer-watch-list, --indexer-watch-add, or --indexer-watch-remove")
        if args.indexer_watch_list:
            report, _state = build_stateful_report(config)
            print(format_indexer_watch_status(report))
            return 0
        try:
            watch_state = update_indexer_watch_config(
                args.config,
                add_address=args.indexer_watch_add,
                remove_address=args.indexer_watch_remove,
                label=args.indexer_watch_label,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"indexer watch update failed: {exc}")
            return 2
        updated = dict(config)
        updated["indexer_watch"] = watch_state
        print(format_indexer_watchlist(updated))
        return 0
    if args.discord_command:
        return discord_command(
            config,
            args.discord_command,
            config_path=args.config,
            mute_minutes=args.discord_mute_minutes,
            reason=args.maintenance_reason,
            query_value=args.discord_query,
            tx_id=args.discord_tx_id,
            amount_kas=args.discord_amount_kas,
        )
    if args.maintenance_status:
        print(format_maintenance_status(config))
        return 0
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
    if args.stream_page:
        return stream_page(config)
    if args.recover:
        return recover(config, force=args.force_recover, dry_run=args.dry_run)
    if args.benchmark_snapshot:
        return benchmark_snapshot(config)
    if args.bps_highway_snapshot:
        return bps_highway_snapshot(config)
    if args.benchmark_report:
        return benchmark_report(config, limit=args.benchmark_limit)
    if args.market_summary:
        return market_summary(timeout=args.market_timeout)
    if args.market_snapshot:
        return market_snapshot(config, timeout=args.market_timeout)
    if args.market_risk_drill:
        return market_risk_drill(
            config,
            score=args.market_risk_score,
            reason=args.market_risk_reason,
            direction=args.market_risk_direction,
        )
    if args.indexer_tx:
        return indexer_lookup(config, "tx", args.indexer_tx)
    if args.indexer_address:
        return indexer_lookup(config, "address", args.indexer_address)
    if args.indexer_balance:
        return indexer_lookup(config, "balance", args.indexer_balance)
    if args.indexer_utxos:
        return indexer_lookup(config, "utxos", args.indexer_utxos)
    if args.indexer_search:
        return indexer_lookup(config, "search", args.indexer_search)
    if args.prometheus:
        return prometheus(config)
    if args.validate_config:
        return validate_config(config)
    if args.prune_state:
        return prune_state(config)
    return status(config)


if __name__ == "__main__":
    raise SystemExit(main())
