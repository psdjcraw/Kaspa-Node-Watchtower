#!/usr/bin/env python3
"""Small local health reporter for a Kaspa node."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_CONFIG = {
    "node_name": "kaspa-local",
    "process_match": "kaspad",
    "log_scan_bytes": 100_000_000,
    "log_path": "",
    "data_dir": "",
    "rpc_endpoint": "",
}


@dataclass(frozen=True)
class IbdCompletion:
    timestamp: str
    blocks: int


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


def parse_trusted_blocks(lines: Iterable[str]) -> int:
    pattern = re.compile(r"Starting to process (\d+) trusted blocks")
    total = 0
    for line in lines:
        match = pattern.search(line)
        if match:
            total += int(match.group(1))
    return total


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


def status(config: dict) -> int:
    log_path = Path(config.get("log_path") or "")
    processes = find_processes(config["process_match"])

    print(f"Kaspa Node Watchtower: {config['node_name']}")
    print(f"RPC endpoint: {config.get('rpc_endpoint') or 'not configured'}")
    print(f"Process match: {config['process_match']}")
    print("Processes:")
    print(format_processes(processes))
    print(f"Data dir size: {dir_size(config.get('data_dir', ''))}")

    if not log_path.exists():
        print(f"Log file: missing ({log_path})")
        return 2

    lines = tail_lines(log_path, int(config["log_scan_bytes"]))
    completions = parse_ibd_completions(lines)
    trusted_blocks = parse_trusted_blocks(lines)
    ibd_total = sum(item.blocks for item in completions)

    print(f"Log file: {log_path}")
    print(f"IBD/catch-up completed block bodies: {ibd_total:,}")
    print(f"IBD completion events: {len(completions)}")
    if completions:
        print(f"Latest IBD completion: {completions[-1].timestamp} ({completions[-1].blocks:,} blocks)")
    print(f"Trusted blocks observed: {trusted_blocks:,}")

    latest_relay = latest_matching(lines, " via relay")
    latest_throughput = latest_matching(lines, "Tx throughput stats:")
    if latest_relay:
        print(f"Latest relay block: {latest_relay}")
    if latest_throughput:
        print(f"Latest throughput: {latest_throughput}")

    return 0 if processes else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Report local Kaspa node health.")
    parser.add_argument("-c", "--config", type=Path, help="Path to config JSON.")
    args = parser.parse_args()
    return status(load_config(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
