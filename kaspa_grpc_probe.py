#!/usr/bin/env python3
"""Fetch read-only metrics from a rusty-kaspa gRPC endpoint."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

GENERATED_PROTO_DIR = Path(__file__).resolve().parent / "generated_proto"
sys.path.insert(0, str(GENERATED_PROTO_DIR))

import grpc  # type: ignore[import-not-found]
import messages_pb2  # type: ignore[import-not-found]
import messages_pb2_grpc  # type: ignore[import-not-found]
import rpc_pb2  # type: ignore[import-not-found]


def _requests() -> list[Any]:
    return [
        messages_pb2.KaspadRequest(id=1, getInfoRequest=rpc_pb2.GetInfoRequestMessage()),
        messages_pb2.KaspadRequest(id=2, getServerInfoRequest=rpc_pb2.GetServerInfoRequestMessage()),
        messages_pb2.KaspadRequest(id=3, getBlockDagInfoRequest=rpc_pb2.GetBlockDagInfoRequestMessage()),
        messages_pb2.KaspadRequest(id=4, getConnectedPeerInfoRequest=rpc_pb2.GetConnectedPeerInfoRequestMessage()),
        messages_pb2.KaspadRequest(
            id=5,
            getMetricsRequest=rpc_pb2.GetMetricsRequestMessage(
                processMetrics=True,
                connectionMetrics=True,
                consensusMetrics=True,
                storageMetrics=True,
            ),
        ),
        messages_pb2.KaspadRequest(id=6, getSyncStatusRequest=rpc_pb2.GetSyncStatusRequestMessage()),
        messages_pb2.KaspadRequest(
            id=7,
            estimateNetworkHashesPerSecondRequest=rpc_pb2.EstimateNetworkHashesPerSecondRequestMessage(
                windowSize=1000,
            ),
        ),
    ]


def _request_iter() -> Any:
    for request in _requests():
        yield request


def _has_error(message: Any) -> str | None:
    try:
        if message.HasField("error") and message.error.message:
            return message.error.message
    except ValueError:
        return None
    return None


def fetch_grpc_metrics(endpoint: str, timeout: float = 5.0) -> dict[str, Any]:
    metrics: dict[str, Any] = {"ok": False, "endpoint": endpoint}
    channel = grpc.insecure_channel(endpoint)
    try:
        stub = messages_pb2_grpc.RPCStub(channel)
        responses: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for response in stub.MessageStream(_request_iter(), timeout=timeout):
            payload = response.WhichOneof("payload")
            if not payload:
                continue
            message = getattr(response, payload)
            error = _has_error(message)
            if error:
                errors[payload] = error
            responses[payload] = message

        get_info = responses.get("getInfoResponse")
        server_info = responses.get("getServerInfoResponse")
        dag = responses.get("getBlockDagInfoResponse")
        peers = responses.get("getConnectedPeerInfoResponse")
        raw_metrics = responses.get("getMetricsResponse")
        sync = responses.get("getSyncStatusResponse")
        network_hashrate = responses.get("estimateNetworkHashesPerSecondResponse")

        peer_infos = list(peers.infos) if peers is not None else []
        user_agents = Counter(peer.userAgent for peer in peer_infos)
        outbound_peers = sum(1 for peer in peer_infos if peer.isOutbound)

        process_metrics = raw_metrics.processMetrics if raw_metrics is not None else None
        connection_metrics = raw_metrics.connectionMetrics if raw_metrics is not None else None
        consensus_metrics = raw_metrics.consensusMetrics if raw_metrics is not None else None

        metrics.update(
            {
                "ok": True,
                "errors": errors,
                "p2p_id": getattr(get_info, "p2pId", ""),
                "server_version": getattr(server_info, "serverVersion", None)
                or getattr(get_info, "serverVersion", ""),
                "network_id": getattr(server_info, "networkId", ""),
                "is_synced": bool(
                    getattr(sync, "isSynced", False)
                    or getattr(server_info, "isSynced", False)
                    or getattr(get_info, "isSynced", False)
                ),
                "mempool_size": int(getattr(get_info, "mempoolSize", 0)),
                "virtual_daa_score": int(
                    getattr(server_info, "virtualDaaScore", 0)
                    or getattr(dag, "virtualDaaScore", 0)
                ),
                "block_count": int(getattr(dag, "blockCount", 0)),
                "header_count": int(getattr(dag, "headerCount", 0)),
                "tip_count": len(getattr(dag, "tipHashes", [])),
                "virtual_parent_count": len(getattr(dag, "virtualParentHashes", [])),
                "difficulty": float(getattr(dag, "difficulty", 0.0)),
                "network_hashes_per_second": int(
                    getattr(network_hashrate, "networkHashesPerSecond", 0)
                )
                if network_hashrate is not None
                else None,
                "network_hashrate_window_size": 1000,
                "pruning_point_hash": getattr(dag, "pruningPointHash", ""),
                "peer_count": len(peer_infos),
                "outbound_peer_count": outbound_peers,
                "inbound_peer_count": len(peer_infos) - outbound_peers,
                "peer_user_agents": dict(user_agents),
                "active_peers": int(getattr(connection_metrics, "activePeers", 0))
                if connection_metrics is not None
                else None,
                "process": {
                    "resident_set_gib": round(
                        float(getattr(process_metrics, "residentSetSize", 0)) / (1024**3),
                        2,
                    )
                    if process_metrics is not None
                    else None,
                    "cpu_usage": float(getattr(process_metrics, "cpuUsage", 0.0))
                    if process_metrics is not None
                    else None,
                    "fd_num": int(getattr(process_metrics, "fdNum", 0))
                    if process_metrics is not None
                    else None,
                },
                "consensus": {
                    "block_count": int(getattr(consensus_metrics, "blockCount", 0))
                    if consensus_metrics is not None
                    else None,
                    "header_count": int(getattr(consensus_metrics, "headerCount", 0))
                    if consensus_metrics is not None
                    else None,
                    "mempool_size": int(getattr(consensus_metrics, "mempoolSize", 0))
                    if consensus_metrics is not None
                    else None,
                    "virtual_daa_score": int(getattr(consensus_metrics, "virtualDaaScore", 0))
                    if consensus_metrics is not None
                    else None,
                },
            }
        )
        return metrics
    except Exception as exc:
        metrics["error"] = str(exc)
        return metrics
    finally:
        channel.close()
