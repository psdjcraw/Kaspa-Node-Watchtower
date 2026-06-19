#!/usr/bin/env python3
"""Fetch read-only metrics through the Kaspa Python SDK wRPC client."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Kaspa node through the Kaspa Python SDK.")
    parser.add_argument("--endpoint", default="127.0.0.1:17110", help="wRPC endpoint, with or without ws://.")
    parser.add_argument("--network-id", default="mainnet")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--encoding", default="borsh", choices=["borsh", "json"])
    args = parser.parse_args()
    print(
        json.dumps(
            fetch_sdk_metrics(args.endpoint, args.network_id, args.timeout, args.encoding),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
