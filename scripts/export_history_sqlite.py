#!/usr/bin/env python3
"""Export watchtower JSONL history files into a local SQLite database."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
from contextlib import closing
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
    "latest_processed_age_seconds",
    "latest_processed_transactions_per_second",
    "latest_processed_transactions",
    "latest_processed_blocks",
    "latest_processed_seconds",
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

RECOVERY_COLUMNS = [
    "started_at",
    "completed_at",
    "node_name",
    "action",
    "reason",
    "mode",
    "force",
    "dry_run",
    "status_before",
    "severity_before",
    "failed_checks_before",
    "status_after",
    "severity_after",
    "failed_checks_after",
    "exit_code",
    "restart_command",
    "data_json",
]

MARKET_COLUMNS = [
    "checked_at",
    "source",
    "ok",
    "error",
    "spot_last_price",
    "spot_change_24h",
    "spot_high_24h",
    "spot_low_24h",
    "spot_volume_24h",
    "futures_mark_price",
    "futures_index_price",
    "futures_basis_pct",
    "futures_funding_rate",
    "futures_funding_apr_pct",
    "futures_next_funding_time",
    "futures_open_interest",
    "futures_open_interest_value",
    "futures_volume_24h",
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
    if key in {"failed_checks_before", "failed_checks_after", "restart_command"}:
        return json.dumps(item.get(key) or [], sort_keys=True)
    if key in {"force", "dry_run", "ok"}:
        return 1 if item.get(key) else 0
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
          latest_processed_age_seconds real,
          latest_processed_transactions_per_second real,
          latest_processed_transactions integer,
          latest_processed_blocks integer,
          latest_processed_seconds real,
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

        create table if not exists recovery_attempts (
          started_at text primary key,
          completed_at text,
          node_name text,
          action text,
          reason text,
          mode text,
          force integer,
          dry_run integer,
          status_before text,
          severity_before text,
          failed_checks_before text,
          status_after text,
          severity_after text,
          failed_checks_after text,
          exit_code integer,
          restart_command text,
          data_json text not null
        );

        create table if not exists market_snapshots (
          checked_at text primary key,
          source text,
          ok integer,
          error text,
          spot_last_price real,
          spot_change_24h real,
          spot_high_24h real,
          spot_low_24h real,
          spot_volume_24h real,
          futures_mark_price real,
          futures_index_price real,
          futures_basis_pct real,
          futures_funding_rate real,
          futures_funding_apr_pct real,
          futures_next_funding_time text,
          futures_open_interest real,
          futures_open_interest_value real,
          futures_volume_24h real,
          data_json text not null
        );
        """
    )
    ensure_columns(
        connection,
        "benchmark_snapshots",
        {
            "latest_processed_age_seconds": "real",
            "latest_processed_transactions_per_second": "real",
            "latest_processed_transactions": "integer",
            "latest_processed_blocks": "integer",
            "latest_processed_seconds": "real",
        },
    )


def ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {
        row[1]
        for row in connection.execute(f"pragma table_info({table})")
    }
    for name, column_type in columns.items():
        if name not in existing:
            try:
                connection.execute(f"alter table {table} add column {name} {column_type}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise


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


def parse_checked_at(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_ratio(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value * 100:.1f}%"


def format_optional_number(value: float | None) -> str:
    if value is None:
        return "unknown"
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def format_gib(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.2f} GiB"


def format_per_second(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{format_optional_number(value)}/s"


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


def format_market_price(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    if abs(parsed) >= 1:
        return f"${parsed:,.2f}"
    return f"${parsed:.5f}"


def format_market_fraction_percent(value: Any) -> str:
    parsed = numeric(value)
    if parsed is None:
        return "unknown"
    return f"{parsed * 100:+.2f}%"


def format_market_percent(value: Any) -> str:
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


def safe_archive_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip())
    return cleaned.strip("-") or "history-archive"


def default_archive_label() -> str:
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"history-{generated_at}"


def latest_window(rows: list[sqlite3.Row], days: int) -> list[sqlite3.Row]:
    dated_rows = [
        (parsed, row)
        for row in rows
        if (parsed := parse_checked_at(row["checked_at"] if "checked_at" in row.keys() else row[0])) is not None
    ]
    if not dated_rows:
        return []
    latest_at = max(parsed for parsed, _row in dated_rows)
    cutoff = latest_at - dt.timedelta(days=days)
    return [row for parsed, row in dated_rows if parsed >= cutoff]


def history_summary(connection: sqlite3.Connection, days: int) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    benchmark_rows = list(
        connection.execute(
            """
            select checked_at, node_name, status, severity, peer_count,
                   virtual_daa_score, block_count, disk_free_gb,
                   latest_processed_transactions_per_second,
                   latest_processed_age_seconds
            from benchmark_snapshots
            order by checked_at
            """
        )
    )
    benchmark_window = latest_window(benchmark_rows, days)
    if benchmark_window and benchmark_window[-1]["node_name"]:
        latest_node_name = benchmark_window[-1]["node_name"]
        benchmark_window = [row for row in benchmark_window if row["node_name"] == latest_node_name]
    latest_benchmark = benchmark_window[-1] if benchmark_window else None
    ok_count = sum(1 for row in benchmark_window if row["severity"] == "ok" and row["status"] == "ok")
    warn_count = sum(1 for row in benchmark_window if row["severity"] == "warn")
    critical_count = sum(1 for row in benchmark_window if row["severity"] == "critical")
    peers = [value for row in benchmark_window if (value := numeric(row["peer_count"])) is not None]
    disks = [value for row in benchmark_window if (value := numeric(row["disk_free_gb"])) is not None]
    daa_values = [value for row in benchmark_window if (value := numeric(row["virtual_daa_score"])) is not None]
    block_values = [value for row in benchmark_window if (value := numeric(row["block_count"])) is not None]
    processed_rates = [
        value
        for row in benchmark_window
        if (value := numeric(row["latest_processed_transactions_per_second"])) is not None
    ]
    processed_ages = [
        value
        for row in benchmark_window
        if (value := numeric(row["latest_processed_age_seconds"])) is not None
    ]
    market_rows = list(
        connection.execute(
            """
            select checked_at, source, ok, error, spot_last_price, spot_change_24h,
                   spot_volume_24h, futures_basis_pct, futures_funding_rate,
                   futures_funding_apr_pct, futures_open_interest,
                   futures_open_interest_value, futures_volume_24h
            from market_snapshots
            order by checked_at
            """
        )
    )
    market_window = latest_window(market_rows, days)
    successful_market = [row for row in market_window if row["ok"]]
    latest_market = successful_market[-1] if successful_market else None
    market_basis = [
        value
        for row in successful_market
        if (value := numeric(row["futures_basis_pct"])) is not None
    ]
    market_funding = [
        value
        for row in successful_market
        if (value := numeric(row["futures_funding_rate"])) is not None
    ]

    recovery_rows = list(
        connection.execute(
            """
            select started_at, action, dry_run, exit_code
            from recovery_attempts
            order by started_at
            """
        )
    )
    recovery_window = latest_window(
        [
            {
                "checked_at": row["started_at"],
                "started_at": row["started_at"],
                "action": row["action"],
                "dry_run": row["dry_run"],
                "exit_code": row["exit_code"],
            }
            for row in recovery_rows
        ],
        days,
    )
    latest_recovery = recovery_window[-1] if recovery_window else None

    upgrade_rows = list(
        connection.execute(
            """
            select recorded_at, phase, label, git_revision
            from upgrade_checkpoints
            order by recorded_at
            """
        )
    )
    upgrade_window = latest_window(
        [
            {
                "checked_at": row["recorded_at"],
                "recorded_at": row["recorded_at"],
                "phase": row["phase"],
                "label": row["label"],
                "git_revision": row["git_revision"],
            }
            for row in upgrade_rows
        ],
        days,
    )
    latest_upgrade = upgrade_window[-1] if upgrade_window else None

    return {
        "window_days": days,
        "benchmark_snapshots": len(benchmark_window),
        "latest_checked_at": latest_benchmark["checked_at"] if latest_benchmark else "none",
        "latest_status": latest_benchmark["status"] if latest_benchmark else "unknown",
        "latest_severity": latest_benchmark["severity"] if latest_benchmark else "unknown",
        "ok_ratio": None if not benchmark_window else ok_count / len(benchmark_window),
        "warn_snapshots": warn_count,
        "critical_snapshots": critical_count,
        "min_peer_count": min(peers) if peers else None,
        "min_disk_free_gb": min(disks) if disks else None,
        "daa_delta": None if len(daa_values) < 2 else daa_values[-1] - daa_values[0],
        "block_delta": None if len(block_values) < 2 else block_values[-1] - block_values[0],
        "latest_processed_tx_rate": None
        if latest_benchmark is None
        else numeric(latest_benchmark["latest_processed_transactions_per_second"]),
        "avg_processed_tx_rate": None
        if not processed_rates
        else sum(processed_rates) / len(processed_rates),
        "max_processed_age_seconds": max(processed_ages) if processed_ages else None,
        "market_snapshots": len(market_window),
        "market_successful_snapshots": len(successful_market),
        "latest_market_checked_at": latest_market["checked_at"] if latest_market else "none",
        "latest_market_source": latest_market["source"] if latest_market else "unknown",
        "latest_spot_price": None if latest_market is None else numeric(latest_market["spot_last_price"]),
        "latest_spot_change_24h": None if latest_market is None else numeric(latest_market["spot_change_24h"]),
        "latest_spot_volume_24h": None if latest_market is None else numeric(latest_market["spot_volume_24h"]),
        "latest_futures_basis_pct": None if latest_market is None else numeric(latest_market["futures_basis_pct"]),
        "avg_futures_basis_pct": None if not market_basis else sum(market_basis) / len(market_basis),
        "latest_futures_funding_rate": None if latest_market is None else numeric(latest_market["futures_funding_rate"]),
        "avg_futures_funding_rate": None if not market_funding else sum(market_funding) / len(market_funding),
        "latest_futures_funding_apr_pct": None
        if latest_market is None
        else numeric(latest_market["futures_funding_apr_pct"]),
        "latest_futures_open_interest": None
        if latest_market is None
        else numeric(latest_market["futures_open_interest"]),
        "latest_futures_open_interest_value": None
        if latest_market is None
        else numeric(latest_market["futures_open_interest_value"]),
        "latest_futures_volume_24h": None if latest_market is None else numeric(latest_market["futures_volume_24h"]),
        "recovery_attempts": len(recovery_window),
        "recovery_executed": sum(1 for row in recovery_window if not row["dry_run"]),
        "recovery_dry_runs": sum(1 for row in recovery_window if row["dry_run"]),
        "last_recovery": latest_recovery,
        "upgrade_checkpoints": len(upgrade_window),
        "latest_upgrade": latest_upgrade,
    }


def summarize_benchmark_window(rows: list[sqlite3.Row], days: int) -> list[dict[str, Any]]:
    window = latest_window(rows, days)
    by_node: dict[str, list[sqlite3.Row]] = {}
    for row in window:
        node_name = row["node_name"] or "unknown"
        by_node.setdefault(node_name, []).append(row)
    summaries = []
    for node_name in sorted(by_node):
        node_rows = by_node[node_name]
        latest = node_rows[-1]
        ok_count = sum(1 for row in node_rows if row["severity"] == "ok" and row["status"] == "ok")
        peers = [value for row in node_rows if (value := numeric(row["peer_count"])) is not None]
        disks = [value for row in node_rows if (value := numeric(row["disk_free_gb"])) is not None]
        daa_values = [value for row in node_rows if (value := numeric(row["virtual_daa_score"])) is not None]
        block_values = [value for row in node_rows if (value := numeric(row["block_count"])) is not None]
        processed_ages = [
            value
            for row in node_rows
            if (value := numeric(row["latest_processed_age_seconds"])) is not None
        ]
        summaries.append(
            {
                "node_name": node_name,
                "snapshots": len(node_rows),
                "latest_checked_at": latest["checked_at"],
                "latest_status": latest["status"],
                "latest_severity": latest["severity"],
                "ok_ratio": None if not node_rows else ok_count / len(node_rows),
                "latest_peer_count": numeric(latest["peer_count"]),
                "min_peer_count": min(peers) if peers else None,
                "min_disk_free_gb": min(disks) if disks else None,
                "latest_daa_score": numeric(latest["virtual_daa_score"]),
                "latest_block_count": numeric(latest["block_count"]),
                "daa_delta": None if len(daa_values) < 2 else daa_values[-1] - daa_values[0],
                "block_delta": None if len(block_values) < 2 else block_values[-1] - block_values[0],
                "latest_processed_tx_rate": numeric(
                    latest["latest_processed_transactions_per_second"]
                ),
                "max_processed_age_seconds": max(processed_ages) if processed_ages else None,
            }
        )
    return summaries


def multi_node_summary(connection: sqlite3.Connection, days: int) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = list(
        connection.execute(
            """
            select checked_at, node_name, status, severity, peer_count,
                   virtual_daa_score, block_count, disk_free_gb,
                   latest_processed_transactions_per_second,
                   latest_processed_age_seconds
            from benchmark_snapshots
            order by checked_at
            """
        )
    )
    return summarize_benchmark_window(rows, days)


def inferred_network(node_name: str) -> str:
    lowered = node_name.lower()
    if "tn10" in lowered or "testnet" in lowered:
        return "tn10"
    if "mainnet" in lowered:
        return "mainnet"
    return "unknown"


def multi_node_comparison(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not summaries:
        return {
            "verdict": "unknown",
            "baseline_nodes": {},
            "nodes": [],
            "lagging_nodes": [],
            "risk_nodes": [],
        }

    by_network: dict[str, list[dict[str, Any]]] = {}
    for item in summaries:
        by_network.setdefault(inferred_network(item["node_name"]), []).append(item)
    baselines = {}
    for network, items in by_network.items():
        scored = [item for item in items if item["latest_daa_score"] is not None]
        baselines[network] = max(scored, key=lambda item: item["latest_daa_score"]) if scored else items[0]

    compared_nodes = []
    lagging_nodes = []
    risk_nodes = []
    worst = "ok"

    for item in summaries:
        network = inferred_network(item["node_name"])
        baseline = baselines[network]
        baseline_daa = baseline["latest_daa_score"]
        baseline_block = baseline["latest_block_count"]
        flags = []
        daa_lag = None
        block_lag = None
        if baseline_daa is not None and item["latest_daa_score"] is not None:
            daa_lag = baseline_daa - item["latest_daa_score"]
            if daa_lag > 120:
                flags.append("daa_lag")
        if baseline_block is not None and item["latest_block_count"] is not None:
            block_lag = baseline_block - item["latest_block_count"]
            if block_lag > 120:
                flags.append("block_lag")
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

        severity_rank = 0
        if item["latest_severity"] == "critical" or "no_peers" in flags:
            severity_rank = 2
        elif flags:
            severity_rank = 1
        if severity_rank == 2:
            worst = "critical"
        elif severity_rank == 1 and worst == "ok":
            worst = "warn"

        compared = {
            **item,
            "network": network,
            "baseline_node": baseline["node_name"],
            "daa_lag": daa_lag,
            "block_lag": block_lag,
            "flags": sorted(set(flags)),
        }
        compared_nodes.append(compared)
        if "daa_lag" in flags or "block_lag" in flags:
            lagging_nodes.append(item["node_name"])
        if flags:
            risk_nodes.append(item["node_name"])

    return {
        "verdict": worst,
        "baseline_nodes": {network: item["node_name"] for network, item in sorted(baselines.items())},
        "nodes": compared_nodes,
        "lagging_nodes": sorted(set(lagging_nodes)),
        "risk_nodes": sorted(set(risk_nodes)),
    }


def print_history_summary(summary: dict[str, Any]) -> None:
    print("== History Summary ==")
    print(f"window_days={summary['window_days']}")
    print(f"benchmark_snapshots={summary['benchmark_snapshots']}")
    print(f"latest_checked_at={summary['latest_checked_at']}")
    print(f"latest_status={summary['latest_status']} latest_severity={summary['latest_severity']}")
    print(f"benchmark_ok_ratio={format_ratio(summary['ok_ratio'])}")
    print(f"benchmark_warn_snapshots={summary['warn_snapshots']}")
    print(f"benchmark_critical_snapshots={summary['critical_snapshots']}")
    print(f"benchmark_min_peer_count={format_optional_number(summary['min_peer_count'])}")
    print(f"benchmark_min_disk_free={format_gib(summary['min_disk_free_gb'])}")
    print(f"benchmark_daa_delta={format_optional_number(summary['daa_delta'])}")
    print(f"benchmark_block_delta={format_optional_number(summary['block_delta'])}")
    print(f"processed_latest_tx_rate={format_per_second(summary['latest_processed_tx_rate'])}")
    print(f"processed_avg_tx_rate={format_per_second(summary['avg_processed_tx_rate'])}")
    print(f"processed_max_age_seconds={format_optional_number(summary['max_processed_age_seconds'])}")
    print(f"market_snapshots={summary['market_snapshots']} successful={summary['market_successful_snapshots']}")
    print(
        "market_latest="
        f"{summary['latest_market_checked_at']} "
        f"source={summary['latest_market_source']} "
        f"spot={format_market_price(summary['latest_spot_price'])} "
        f"24h={format_market_fraction_percent(summary['latest_spot_change_24h'])} "
        f"volume={format_market_volume(summary['latest_spot_volume_24h'])}"
    )
    print(
        "market_futures="
        f"basis={format_market_percent(summary['latest_futures_basis_pct'])} "
        f"basis_avg={format_market_percent(summary['avg_futures_basis_pct'])} "
        f"funding={format_market_fraction_percent(summary['latest_futures_funding_rate'])} "
        f"funding_avg={format_market_fraction_percent(summary['avg_futures_funding_rate'])} "
        f"funding_apr={format_market_percent(summary['latest_futures_funding_apr_pct'])}"
    )
    print(
        "market_futures_positioning="
        f"open_interest={format_market_volume(summary['latest_futures_open_interest'])} "
        f"oi_value={format_market_usdt(summary['latest_futures_open_interest_value'])} "
        f"volume_24h={format_market_volume(summary['latest_futures_volume_24h'])}"
    )
    print(
        "recovery_attempts="
        f"{summary['recovery_attempts']} "
        f"executed={summary['recovery_executed']} "
        f"dry_runs={summary['recovery_dry_runs']}"
    )
    latest_recovery = summary["last_recovery"]
    if latest_recovery:
        print(
            "last_recovery="
            f"{latest_recovery['started_at']} "
            f"action={latest_recovery['action']} "
            f"exit_code={latest_recovery['exit_code']}"
        )
    else:
        print("last_recovery=none")
    print(f"upgrade_checkpoints={summary['upgrade_checkpoints']}")
    latest_upgrade = summary["latest_upgrade"]
    if latest_upgrade:
        print(
            "latest_upgrade="
            f"{latest_upgrade['recorded_at']} "
            f"phase={latest_upgrade['phase']} "
            f"label={latest_upgrade['label']} "
            f"revision={latest_upgrade['git_revision']}"
        )
    else:
        print("latest_upgrade=none")


def print_multi_node_summary(summaries: list[dict[str, Any]], days: int) -> None:
    comparison = multi_node_comparison(summaries)
    print("== Multi-Node History Summary ==")
    print(f"window_days={days}")
    if not summaries:
        print("nodes=0")
        return
    print(f"nodes={len(summaries)}")
    baseline_text = ",".join(
        f"{network}:{node}" for network, node in comparison["baseline_nodes"].items()
    )
    print(
        "verdict="
        f"{comparison['verdict']} "
        f"baselines={baseline_text or 'none'} "
        f"lagging_nodes={','.join(comparison['lagging_nodes']) or 'none'} "
        f"risk_nodes={','.join(comparison['risk_nodes']) or 'none'}"
    )
    for item in comparison["nodes"]:
        print(
            "node="
            f"{item['node_name']} "
            f"network={item['network']} "
            f"snapshots={item['snapshots']} "
            f"latest={item['latest_checked_at']} "
            f"status={item['latest_status']} "
            f"severity={item['latest_severity']} "
            f"ok_ratio={format_ratio(item['ok_ratio'])} "
            f"min_peers={format_optional_number(item['min_peer_count'])} "
            f"min_disk={format_gib(item['min_disk_free_gb'])} "
            f"daa_lag={format_optional_number(item['daa_lag'])} "
            f"block_lag={format_optional_number(item['block_lag'])} "
            f"daa_delta={format_optional_number(item['daa_delta'])} "
            f"block_delta={format_optional_number(item['block_delta'])} "
            f"processed_tx_rate={format_per_second(item['latest_processed_tx_rate'])} "
            f"processed_max_age_seconds={format_optional_number(item['max_processed_age_seconds'])} "
            f"flags={','.join(item['flags']) or 'none'}"
        )


def copy_if_exists(source: Path, target_dir: Path, target_name: str) -> str | None:
    if not source.exists():
        return None
    target = target_dir / target_name
    shutil.copy2(source, target)
    return target.name


def write_archive(
    *,
    archive_dir: Path,
    archive_label: str | None,
    db_path: Path,
    benchmark_path: Path,
    market_path: Path,
    upgrade_path: Path,
    recovery_path: Path,
    counts: dict[str, int],
    summary: dict[str, Any],
) -> Path:
    label = safe_archive_label(archive_label or default_archive_label())
    target_dir = archive_dir / label
    target_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "database": copy_if_exists(db_path, target_dir, "watchtower-history.sqlite"),
        "benchmarks": copy_if_exists(benchmark_path, target_dir, "benchmarks.jsonl"),
        "market": copy_if_exists(market_path, target_dir, "market-snapshots.jsonl"),
        "upgrades": copy_if_exists(upgrade_path, target_dir, "upgrade-checkpoints.jsonl"),
        "recovery": copy_if_exists(recovery_path, target_dir, "recovery-history.jsonl"),
    }
    summary_path = target_dir / f"history-summary-{summary['window_days']}d.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files["summary"] = summary_path.name

    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "archive_label": label,
        "window_days": summary["window_days"],
        "counts": counts,
        "files": files,
        "sources": {
            "database": str(db_path),
            "benchmarks": str(benchmark_path),
            "market": str(market_path),
            "upgrades": str(upgrade_path),
            "recovery": str(recovery_path),
        },
    }
    manifest_path = target_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target_dir


def export(args: argparse.Namespace) -> int:
    args.days = max(1, args.days)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    benchmark_items = load_jsonl(args.benchmarks)
    market_path = getattr(args, "market", Path("state/market-snapshots.jsonl"))
    market_items = load_jsonl(market_path)
    upgrade_items = load_jsonl(args.upgrades)
    recovery_items = load_jsonl(args.recovery)
    with closing(sqlite3.connect(args.db)) as connection:
        create_schema(connection)
        benchmark_imported = upsert_items(
            connection,
            "benchmark_snapshots",
            BENCHMARK_COLUMNS,
            "checked_at",
            benchmark_items,
        )
        market_imported = upsert_items(
            connection,
            "market_snapshots",
            MARKET_COLUMNS,
            "checked_at",
            market_items,
        )
        upgrade_imported = upsert_items(
            connection,
            "upgrade_checkpoints",
            UPGRADE_COLUMNS,
            "recorded_at",
            upgrade_items,
        )
        recovery_imported = upsert_items(
            connection,
            "recovery_attempts",
            RECOVERY_COLUMNS,
            "started_at",
            recovery_items,
        )
        connection.commit()
        benchmark_count = table_count(connection, "benchmark_snapshots")
        market_count = table_count(connection, "market_snapshots")
        upgrade_count = table_count(connection, "upgrade_checkpoints")
        recovery_count = table_count(connection, "recovery_attempts")
        summary = history_summary(connection, args.days) if args.summary or args.archive_dir else None
        node_summary = multi_node_summary(connection, args.days) if getattr(args, "multi_node_summary", False) else None

    counts = {
        "benchmark_snapshots": benchmark_count,
        "market_snapshots": market_count,
        "upgrade_checkpoints": upgrade_count,
        "recovery_attempts": recovery_count,
        "benchmark_imported": benchmark_imported,
        "market_imported": market_imported,
        "upgrade_imported": upgrade_imported,
        "recovery_imported": recovery_imported,
    }
    archive_path = None
    if args.archive_dir:
        assert summary is not None
        archive_path = write_archive(
            archive_dir=args.archive_dir,
            archive_label=args.archive_label,
            db_path=args.db,
            benchmark_path=args.benchmarks,
            market_path=market_path,
            upgrade_path=args.upgrades,
            recovery_path=args.recovery,
            counts=counts,
            summary=summary,
        )

    print(f"SQLite history written: {args.db}")
    print(f"benchmark_snapshots imported={benchmark_imported} total={benchmark_count}")
    print(f"market_snapshots imported={market_imported} total={market_count}")
    print(f"upgrade_checkpoints imported={upgrade_imported} total={upgrade_count}")
    print(f"recovery_attempts imported={recovery_imported} total={recovery_count}")
    if archive_path:
        print(f"history_archive written: {archive_path}")
    if args.summary and summary is not None:
        print()
        print_history_summary(summary)
    if getattr(args, "multi_node_summary", False) and node_summary is not None:
        print()
        print_multi_node_summary(node_summary, args.days)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export watchtower history JSONL into SQLite.")
    parser.add_argument("--db", type=Path, default=Path("state/watchtower-history.sqlite"))
    parser.add_argument("--benchmarks", type=Path, default=Path("state/benchmarks.jsonl"))
    parser.add_argument("--market", type=Path, default=Path("state/market-snapshots.jsonl"))
    parser.add_argument("--upgrades", type=Path, default=Path("state/upgrade-checkpoints.jsonl"))
    parser.add_argument("--recovery", type=Path, default=Path("state/recovery-history.jsonl"))
    parser.add_argument("--summary", action="store_true", help="Print an operator summary from the SQLite history.")
    parser.add_argument("--multi-node-summary", action="store_true", help="Print per-node history summaries from SQLite.")
    parser.add_argument("--days", type=int, default=7, help="History summary window in days.")
    parser.add_argument(
        "--archive-dir",
        type=Path,
        help="Write a portable history archive with SQLite, source JSONL files, summary JSON, and manifest.",
    )
    parser.add_argument("--archive-label", help="Directory name to use inside --archive-dir.")
    args = parser.parse_args()
    return export(args)


if __name__ == "__main__":
    raise SystemExit(main())
