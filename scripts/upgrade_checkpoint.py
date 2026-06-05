#!/usr/bin/env python3
"""Capture and compare Kaspa watchtower checkpoints around node upgrades."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv/bin/python"
VENV_DIR = REPO_ROOT / ".venv"
if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

sys.path.insert(0, str(REPO_ROOT))

import watchtower  # noqa: E402


def git_revision() -> str:
    completed = subprocess.run(
        ["git", "log", "-1", "--oneline"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip() or "unknown"


def checkpoint_path(config: dict[str, Any]) -> Path:
    state_path = Path(config.get("state_path") or watchtower.DEFAULT_CONFIG["state_path"])
    return state_path.parent / "upgrade-checkpoints.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True))
        handle.write("\n")


def numeric(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def delta(after: Any, before: Any) -> str:
    after_value = numeric(after)
    before_value = numeric(before)
    if after_value is None or before_value is None:
        return "unknown"
    return f"{after_value - before_value:+.2f}"


def record_from_report(phase: str, label: str, report: dict[str, Any]) -> dict[str, Any]:
    grpc = report.get("grpc_metrics") or {}
    process = grpc.get("process") or {}
    progress = report.get("progress") or {}
    disk = report.get("disk") or {}
    return {
        "phase": phase,
        "label": label,
        "recorded_at": dt.datetime.now().astimezone().isoformat(),
        "checked_at": report.get("checked_at"),
        "git_revision": git_revision(),
        "node_name": report.get("node_name"),
        "status": report.get("status"),
        "severity": report.get("severity"),
        "failed_checks": watchtower.failed_check_names(report),
        "grpc_detail": grpc.get("detail") or grpc.get("error"),
        "peer_count": grpc.get("peer_count"),
        "active_peers": grpc.get("active_peers"),
        "is_synced": grpc.get("is_synced"),
        "network_id": grpc.get("network_id"),
        "virtual_daa_score": grpc.get("virtual_daa_score"),
        "block_count": grpc.get("block_count"),
        "header_count": grpc.get("header_count"),
        "mempool_size": grpc.get("mempool_size"),
        "tip_count": grpc.get("tip_count"),
        "difficulty": grpc.get("difficulty"),
        "process_resident_set_gib": process.get("resident_set_gib"),
        "process_cpu_usage": process.get("cpu_usage"),
        "process_fd_num": process.get("fd_num"),
        "relay_blocks_in_window": progress.get("relay_blocks_in_window"),
        "relay_events_in_window": progress.get("relay_events_in_window"),
        "disk_free_gb": disk.get("free_gb"),
        "disk_free_percent": disk.get("free_percent"),
    }


def format_record(record: dict[str, Any], path: Path) -> str:
    failed = ", ".join(record.get("failed_checks") or []) or "none"
    return "\n".join(
        [
            f"Upgrade checkpoint saved: {path}",
            f"phase={record['phase']} label={record['label']} node={record.get('node_name')}",
            f"status={record.get('status')} severity={record.get('severity')} failed_checks={failed}",
            f"grpc_detail={record.get('grpc_detail') or 'none'}",
            (
                "grpc="
                f"synced={record.get('is_synced')} "
                f"peers={record.get('peer_count')} "
                f"daa={record.get('virtual_daa_score')} "
                f"blocks={record.get('block_count')} "
                f"mempool={record.get('mempool_size')}"
            ),
            (
                "resources="
                f"rss_gib={record.get('process_resident_set_gib')} "
                f"fd={record.get('process_fd_num')} "
                f"disk_free_gib={record.get('disk_free_gb')}"
            ),
        ]
    )


def save_checkpoint(config: dict[str, Any], phase: str, label: str) -> int:
    report = build_report_with_retries(config)
    path = checkpoint_path(config)
    record = record_from_report(phase, label, report)
    append_jsonl(path, record)
    print(format_record(record, path))
    return 0 if record["status"] == "ok" else 1


def build_report_with_retries(config: dict[str, Any], attempts: int = 3) -> dict[str, Any]:
    report = watchtower.build_report(config)
    for _ in range(1, attempts):
        failed = set(watchtower.failed_check_names(report))
        if "grpc_metrics" not in failed:
            return report
        time.sleep(2)
        report = watchtower.build_report(config)
    return report


def latest_phase(items: list[dict[str, Any]], phase: str) -> dict[str, Any] | None:
    for item in reversed(items):
        if item.get("phase") == phase:
            return item
    return None


def report(config: dict[str, Any]) -> int:
    path = checkpoint_path(config)
    items = load_jsonl(path)
    before = latest_phase(items, "before")
    after = latest_phase(items, "after")
    if before is None or after is None:
        print(f"Upgrade checkpoint report unavailable: need before and after in {path}")
        return 2

    lines = [
        f"Upgrade checkpoint report: {before.get('label')} -> {after.get('label')}",
        f"path={path}",
        f"before_status={before.get('status')} severity={before.get('severity')} checked_at={before.get('checked_at')}",
        f"after_status={after.get('status')} severity={after.get('severity')} checked_at={after.get('checked_at')}",
        f"daa_delta={delta(after.get('virtual_daa_score'), before.get('virtual_daa_score'))}",
        f"block_delta={delta(after.get('block_count'), before.get('block_count'))}",
        f"peer_delta={delta(after.get('peer_count'), before.get('peer_count'))}",
        f"mempool_delta={delta(after.get('mempool_size'), before.get('mempool_size'))}",
        f"rss_gib_delta={delta(after.get('process_resident_set_gib'), before.get('process_resident_set_gib'))}",
        f"fd_delta={delta(after.get('process_fd_num'), before.get('process_fd_num'))}",
        f"disk_free_gib_delta={delta(after.get('disk_free_gb'), before.get('disk_free_gb'))}",
    ]
    print("\n".join(lines))
    return 0 if after.get("status") == "ok" else 1


def default_label(phase: str) -> str:
    timestamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"{phase}-{timestamp}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture or compare upgrade checkpoints.")
    parser.add_argument("phase", choices=["before", "after", "report"])
    parser.add_argument("--config", "-c", type=Path, default=Path("config.json"))
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    config = watchtower.load_config(args.config)
    if args.phase == "report":
        return report(config)
    return save_checkpoint(config, args.phase, args.label or default_label(args.phase))


if __name__ == "__main__":
    raise SystemExit(main())
