#!/usr/bin/env python3
"""Export watchtower JSONL history files into a local SQLite database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


BENCHMARK_COLUMNS = [
    "checked_at",
    "node_name",
    "status",
    "severity",
    "peer_count",
    "active_peers",
    "virtual_daa_score",
    "block_count",
    "header_count",
    "mempool_size",
    "tip_count",
    "relay_blocks_in_window",
    "relay_events_in_window",
    "disk_free_gb",
    "disk_free_percent",
    "process_resident_set_gib",
    "process_cpu_usage",
    "process_fd_num",
    "data_json",
]

UPGRADE_COLUMNS = [
    "recorded_at",
    "checked_at",
    "phase",
    "label",
    "node_name",
    "status",
    "severity",
    "git_revision",
    "peer_count",
    "active_peers",
    "virtual_daa_score",
    "block_count",
    "header_count",
    "mempool_size",
    "tip_count",
    "disk_free_gb",
    "process_resident_set_gib",
    "process_cpu_usage",
    "process_fd_num",
    "data_json",
]


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


def value(item: dict[str, Any], key: str) -> Any:
    if key == "data_json":
        return json.dumps(item, sort_keys=True)
    return item.get(key)


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists benchmark_snapshots (
          checked_at text primary key,
          node_name text,
          status text,
          severity text,
          peer_count integer,
          active_peers integer,
          virtual_daa_score integer,
          block_count integer,
          header_count integer,
          mempool_size integer,
          tip_count integer,
          relay_blocks_in_window integer,
          relay_events_in_window integer,
          disk_free_gb real,
          disk_free_percent real,
          process_resident_set_gib real,
          process_cpu_usage real,
          process_fd_num integer,
          data_json text not null
        );

        create table if not exists upgrade_checkpoints (
          recorded_at text primary key,
          checked_at text,
          phase text,
          label text,
          node_name text,
          status text,
          severity text,
          git_revision text,
          peer_count integer,
          active_peers integer,
          virtual_daa_score integer,
          block_count integer,
          header_count integer,
          mempool_size integer,
          tip_count integer,
          disk_free_gb real,
          process_resident_set_gib real,
          process_cpu_usage real,
          process_fd_num integer,
          data_json text not null
        );
        """
    )


def upsert_items(
    connection: sqlite3.Connection,
    table: str,
    columns: list[str],
    key_column: str,
    items: list[dict[str, Any]],
) -> int:
    if not items:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != key_column)
    sql = (
        f"insert into {table} ({column_sql}) values ({placeholders}) "
        f"on conflict({key_column}) do update set {updates}"
    )
    rows = [tuple(value(item, column) for column in columns) for item in items]
    connection.executemany(sql, rows)
    return len(rows)


def table_count(connection: sqlite3.Connection, table: str) -> int:
    cursor = connection.execute(f"select count(*) from {table}")
    return int(cursor.fetchone()[0])


def export(args: argparse.Namespace) -> int:
    args.db.parent.mkdir(parents=True, exist_ok=True)
    benchmark_items = load_jsonl(args.benchmarks)
    upgrade_items = load_jsonl(args.upgrades)
    with sqlite3.connect(args.db) as connection:
        create_schema(connection)
        benchmark_imported = upsert_items(
            connection,
            "benchmark_snapshots",
            BENCHMARK_COLUMNS,
            "checked_at",
            benchmark_items,
        )
        upgrade_imported = upsert_items(
            connection,
            "upgrade_checkpoints",
            UPGRADE_COLUMNS,
            "recorded_at",
            upgrade_items,
        )
        connection.commit()
        benchmark_count = table_count(connection, "benchmark_snapshots")
        upgrade_count = table_count(connection, "upgrade_checkpoints")

    print(f"SQLite history written: {args.db}")
    print(f"benchmark_snapshots imported={benchmark_imported} total={benchmark_count}")
    print(f"upgrade_checkpoints imported={upgrade_imported} total={upgrade_count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export watchtower history JSONL into SQLite.")
    parser.add_argument("--db", type=Path, default=Path("state/watchtower-history.sqlite"))
    parser.add_argument("--benchmarks", type=Path, default=Path("state/benchmarks.jsonl"))
    parser.add_argument("--upgrades", type=Path, default=Path("state/upgrade-checkpoints.jsonl"))
    args = parser.parse_args()
    return export(args)


if __name__ == "__main__":
    raise SystemExit(main())
