#!/usr/bin/env python3
"""Fetch read-only metrics through the Kaspa Python SDK wRPC client."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any


def wrpc_url_from_endpoint(endpoint: str) -> str:
    if endpoint.startswith(("ws://", "wss://")):
        return endpoint
    return f"ws://{endpoint}"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sompi_from_utxo(item: dict[str, Any]) -> int:
    entry = item.get("utxoEntry") or item.get("utxo_entry") or {}
    return _int(entry.get("amount"))


def _tx_id_from_utxo(item: dict[str, Any]) -> str:
    outpoint = item.get("outpoint") or {}
    return str(outpoint.get("transactionId") or outpoint.get("transaction_id") or "")


async def fetch_sdk_metrics_async(
    endpoint: str,
    network_id: str = "mainnet",
    timeout: float = 5.0,
    encoding: str = "borsh",
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "ok": False,
        "sdk_installed": False,
        "endpoint": endpoint,
        "network_id": network_id,
        "encoding": encoding,
    }
    if not endpoint:
        metrics["detail"] = "not configured"
        return metrics

    try:
        from kaspa import RpcClient
    except Exception as exc:
        metrics["detail"] = f"Kaspa Python SDK unavailable: {exc}"
        return metrics

    metrics["sdk_installed"] = True
    url = wrpc_url_from_endpoint(endpoint)
    client = RpcClient(url=url, network_id=network_id, encoding=encoding)
    started = time.monotonic()
    try:
        await asyncio.wait_for(
            client.connect(
                strategy="fallback",
                timeout_duration=max(1, int(timeout * 1000)),
                retry_interval=1000,
            ),
            timeout=timeout + 1.0,
        )
        connect_latency_ms = (time.monotonic() - started) * 1000
        call_started = time.monotonic()
        server, dag, peers, sync = await asyncio.wait_for(
            asyncio.gather(
                client.get_server_info(),
                client.get_block_dag_info(),
                client.get_connected_peer_info(),
                client.get_sync_status(),
            ),
            timeout=timeout,
        )
        rpc_latency_ms = (time.monotonic() - call_started) * 1000
        server = _dict(server)
        dag = _dict(dag)
        peers = _dict(peers)
        sync = _dict(sync)
        peer_infos = peers.get("infos") or []
        if not isinstance(peer_infos, list):
            peer_infos = []

        metrics.update(
            {
                "ok": True,
                "detail": "read ok",
                "url": str(getattr(client, "url", None) or url),
                "is_connected": bool(getattr(client, "is_connected", False)),
                "connect_latency_ms": round(connect_latency_ms, 2),
                "rpc_latency_ms": round(rpc_latency_ms, 2),
                "server_version": server.get("serverVersion") or server.get("server_version") or "",
                "server_network_id": server.get("networkId") or server.get("network_id") or "",
                "is_synced": bool(
                    sync.get("isSynced")
                    if "isSynced" in sync
                    else sync.get("is_synced", server.get("isSynced", server.get("is_synced", False)))
                ),
                "virtual_daa_score": _int(
                    server.get("virtualDaaScore")
                    or server.get("virtual_daa_score")
                    or dag.get("virtualDaaScore")
                    or dag.get("virtual_daa_score")
                ),
                "block_count": _int(dag.get("blockCount") or dag.get("block_count")),
                "header_count": _int(dag.get("headerCount") or dag.get("header_count")),
                "tip_count": len(dag.get("tipHashes") or dag.get("tip_hashes") or []),
                "peer_count": len(peer_infos),
            }
        )
        return metrics
    except Exception as exc:
        metrics["detail"] = f"SDK probe failed: {exc}"
        metrics["error"] = str(exc)
        return metrics
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def fetch_sdk_metrics(
    endpoint: str,
    network_id: str = "mainnet",
    timeout: float = 5.0,
    encoding: str = "borsh",
) -> dict[str, Any]:
    return asyncio.run(fetch_sdk_metrics_async(endpoint, network_id, timeout, encoding))


async def collect_subscription_metrics_async(
    endpoint: str,
    network_id: str = "mainnet",
    timeout: float = 5.0,
    encoding: str = "borsh",
    duration: float = 5.0,
    watch_addresses: list[str] | None = None,
    include_accepted_transaction_ids: bool = True,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "subscription_ok": False,
        "subscription_enabled": True,
        "subscription_duration_seconds": duration,
        "subscription_watch_addresses": len(watch_addresses or []),
        "subscription_events_total": 0,
        "subscription_block_added_total": 0,
        "subscription_virtual_chain_changed_total": 0,
        "subscription_virtual_daa_score_changed_total": 0,
        "subscription_utxos_changed_total": 0,
        "subscription_utxos_added": 0,
        "subscription_utxos_removed": 0,
        "subscription_utxo_added_sompi": 0,
        "subscription_utxo_removed_sompi": 0,
        "subscription_utxo_events": [],
        "subscription_connect_events": 0,
        "subscription_disconnect_events": 0,
        "subscription_last_event_age_seconds": None,
        "subscription_last_virtual_daa_score": None,
    }
    if not endpoint:
        metrics["subscription_detail"] = "not configured"
        return metrics

    try:
        from kaspa import Address, RpcClient
    except Exception as exc:
        metrics["subscription_detail"] = f"Kaspa Python SDK unavailable: {exc}"
        return metrics

    url = wrpc_url_from_endpoint(endpoint)
    client = RpcClient(url=url, network_id=network_id, encoding=encoding)
    watched = [Address(address) for address in watch_addresses or []]
    last_event_monotonic: float | None = None

    def mark_event() -> None:
        nonlocal last_event_monotonic
        metrics["subscription_events_total"] += 1
        last_event_monotonic = time.monotonic()

    def on_connect(_event: dict[str, Any]) -> None:
        metrics["subscription_connect_events"] += 1

    def on_disconnect(_event: dict[str, Any]) -> None:
        metrics["subscription_disconnect_events"] += 1

    def on_block_added(_event: dict[str, Any]) -> None:
        mark_event()
        metrics["subscription_block_added_total"] += 1

    def on_virtual_chain_changed(_event: dict[str, Any]) -> None:
        mark_event()
        metrics["subscription_virtual_chain_changed_total"] += 1

    def on_virtual_daa_score_changed(event: dict[str, Any]) -> None:
        mark_event()
        metrics["subscription_virtual_daa_score_changed_total"] += 1
        data = _dict(event.get("data"))
        score = data.get("virtualDaaScore") or data.get("virtual_daa_score")
        if score is not None:
            metrics["subscription_last_virtual_daa_score"] = _int(score)

    def on_utxos_changed(event: dict[str, Any]) -> None:
        mark_event()
        added = [item for item in event.get("added") or [] if isinstance(item, dict)]
        removed = [item for item in event.get("removed") or [] if isinstance(item, dict)]
        metrics["subscription_utxos_changed_total"] += 1
        metrics["subscription_utxos_added"] += len(added)
        metrics["subscription_utxos_removed"] += len(removed)
        metrics["subscription_utxo_added_sompi"] += sum(_sompi_from_utxo(item) for item in added)
        metrics["subscription_utxo_removed_sompi"] += sum(_sompi_from_utxo(item) for item in removed)
        observed_at = datetime.now(timezone.utc).isoformat()
        for direction, items in (("incoming", added), ("outgoing", removed)):
            for item in items:
                amount_sompi = _sompi_from_utxo(item)
                address = str(item.get("address") or "")
                tx_id = _tx_id_from_utxo(item)
                metrics["subscription_utxo_events"].append(
                    {
                        "observed_at": observed_at,
                        "source": "sdk_subscription",
                        "type": "utxo_changed",
                        "direction": direction,
                        "address": address,
                        "tx_id": tx_id,
                        "amount_sompi": amount_sompi,
                        "amount_kas": amount_sompi / 100_000_000,
                    }
                )

    try:
        client.add_event_listener("connect", on_connect)
        client.add_event_listener("disconnect", on_disconnect)
        client.add_event_listener("block-added", on_block_added)
        client.add_event_listener("virtual-chain-changed", on_virtual_chain_changed)
        client.add_event_listener("virtual-daa-score-changed", on_virtual_daa_score_changed)
        if watched:
            client.add_event_listener("utxos-changed", on_utxos_changed)
        await asyncio.wait_for(
            client.connect(
                strategy="fallback",
                timeout_duration=max(1, int(timeout * 1000)),
                retry_interval=1000,
            ),
            timeout=timeout + 1.0,
        )
        await asyncio.wait_for(client.subscribe_block_added(), timeout=timeout)
        await asyncio.wait_for(
            client.subscribe_virtual_chain_changed(include_accepted_transaction_ids),
            timeout=timeout,
        )
        await asyncio.wait_for(client.subscribe_virtual_daa_score_changed(), timeout=timeout)
        if watched:
            await asyncio.wait_for(client.subscribe_utxos_changed(watched), timeout=timeout)
        await asyncio.sleep(max(0.1, duration))
        metrics["subscription_ok"] = True
        metrics["subscription_detail"] = "read ok"
        if last_event_monotonic is not None:
            metrics["subscription_last_event_age_seconds"] = round(time.monotonic() - last_event_monotonic, 2)
        return metrics
    except Exception as exc:
        metrics["subscription_detail"] = f"SDK subscription probe failed: {exc}"
        metrics["subscription_error"] = str(exc)
        return metrics
    finally:
        try:
            if watched:
                await client.unsubscribe_utxos_changed(watched)
            await client.unsubscribe_virtual_daa_score_changed()
            await client.unsubscribe_virtual_chain_changed(include_accepted_transaction_ids)
            await client.unsubscribe_block_added()
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass


def collect_subscription_metrics(
    endpoint: str,
    network_id: str = "mainnet",
    timeout: float = 5.0,
    encoding: str = "borsh",
    duration: float = 5.0,
    watch_addresses: list[str] | None = None,
    include_accepted_transaction_ids: bool = True,
) -> dict[str, Any]:
    return asyncio.run(
        collect_subscription_metrics_async(
            endpoint,
            network_id,
            timeout,
            encoding,
            duration,
            watch_addresses or [],
            include_accepted_transaction_ids,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Kaspa node through the Kaspa Python SDK.")
    parser.add_argument("--endpoint", default="127.0.0.1:17110", help="wRPC endpoint, with or without ws://.")
    parser.add_argument("--network-id", default="mainnet")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--encoding", default="borsh", choices=["borsh", "json"])
    parser.add_argument("--subscriptions", action="store_true", help="Collect short-lived SDK subscription metrics.")
    parser.add_argument("--duration", type=float, default=5.0, help="Subscription collection duration in seconds.")
    parser.add_argument("--address", action="append", default=[], help="Kaspa address to watch for UTXO changes.")
    args = parser.parse_args()
    if args.subscriptions:
        result = collect_subscription_metrics(
            args.endpoint,
            args.network_id,
            args.timeout,
            args.encoding,
            args.duration,
            args.address,
        )
    else:
        result = fetch_sdk_metrics(args.endpoint, args.network_id, args.timeout, args.encoding)
    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
