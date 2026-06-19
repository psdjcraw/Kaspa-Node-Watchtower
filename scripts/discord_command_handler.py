#!/usr/bin/env python3
"""Local Discord/OpenClaw slash-command adapter for Kaspa Watchtower."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import watchtower  # noqa: E402


COMMANDS = {
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
    "watch-add",
    "watch-remove",
    "watch-test",
    "mute",
    "mute-all",
    "unmute",
}


def option_value(options: list[dict[str, Any]], name: str) -> Any:
    for option in options:
        if option.get("name") == name:
            return option.get("value")
    return None


def parse_discord_payload(payload: dict[str, Any]) -> tuple[str, float, str, str]:
    data = payload.get("data") if "data" in payload else payload
    options = data.get("options") or []
    command = str(payload.get("command") or data.get("command") or data.get("name") or "status")
    minutes: float = 30
    reason = ""
    query = ""

    # Discord subcommands arrive as an option with nested options.
    if options and options[0].get("type") == 1:
        parent_command = command
        subcommand = options[0]
        command = str(subcommand.get("name") or command)
        options = subcommand.get("options") or []
        if parent_command == "watch":
            command = f"watch-{command}"

    minutes_value = payload.get("minutes", option_value(options, "minutes"))
    reason_value = payload.get("reason", option_value(options, "reason"))
    label_value = payload.get("label", option_value(options, "label"))
    query_value = (
        payload.get("query")
        or payload.get("tx_id")
        or payload.get("address")
        or option_value(options, "query")
        or option_value(options, "tx_id")
        or option_value(options, "address")
    )
    if minutes_value is not None:
        minutes = float(minutes_value)
    if reason_value is not None:
        reason = str(reason_value)
    if label_value is not None:
        reason = str(label_value)
    if query_value is not None:
        query = str(query_value)

    if command == "mute_all":
        command = "mute-all"
    if command in {"wallet_txs", "wallettxs"}:
        command = "wallet-txs"
    if command in {"watch_list", "watchlist"}:
        command = "watch-list"
    if command in {"watch_check", "watchcheck"}:
        command = "watch-check"
    if command in {"watch_add", "watchadd"}:
        command = "watch-add"
    if command in {"watch_remove", "watchremove"}:
        command = "watch-remove"
    if command in {"watch_test", "watchtest"}:
        command = "watch-test"
    if command not in COMMANDS:
        raise ValueError(f"unsupported Discord command: {command}")
    return command, minutes, reason, query


def load_payload(path: str) -> dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Kaspa Watchtower Discord command locally.")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS), help="Command to run.")
    parser.add_argument("-c", "--config", type=Path, default=Path("config.json"), help="Path to config JSON.")
    parser.add_argument("--minutes", type=float, default=30, help="Mute window for mute commands.")
    parser.add_argument("--reason", default="", help="Maintenance reason for mute commands.")
    parser.add_argument("--query", default="", help="Lookup value for tx/address/search commands.")
    parser.add_argument(
        "--payload",
        help="Read a Discord/OpenClaw interaction JSON payload from this path, or '-' for stdin.",
    )
    args = parser.parse_args()

    command = args.command or "status"
    minutes = args.minutes
    reason = args.reason
    query = args.query
    if args.payload:
        try:
            command, minutes, reason, query = parse_discord_payload(load_payload(args.payload))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"discord handler failed: {exc}")
            return 2

    config = watchtower.load_config(args.config)
    return watchtower.discord_command(
        config,
        command,
        config_path=args.config,
        mute_minutes=minutes,
        reason=reason,
        query_value=query,
    )


if __name__ == "__main__":
    raise SystemExit(main())
