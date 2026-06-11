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


def _balance_requests(addresses: list[str]) -> list[Any]:
    return [
        messages_pb2.KaspadRequest(
            id=1,
            getBalancesByAddressesRequest=rpc_pb2.GetBalancesByAddressesRequestMessage(
                addresses=addresses,
            ),
        )
    ]


def _mempool_requests(addresses: list[str]) -> list[Any]:
    return [
        messages_pb2.KaspadRequest(
            id=1,
            getMempoolEntriesByAddressesRequest=rpc_pb2.GetMempoolEntriesByAddressesRequestMessage(
                addresses=addresses,
                includeOrphanPool=True,
                filterTransactionPool=True,
            ),
        )
    ]


def _all_mempool_requests() -> list[Any]:
    return [
        messages_pb2.KaspadRequest(
            id=1,
            getMempoolEntriesRequest=rpc_pb2.GetMempoolEntriesRequestMessage(
                includeOrphanPool=True,
                filterTransactionPool=True,
            ),
        )
    ]


def _virtual_chain_requests(start_hash: str) -> list[Any]:
    return [
        messages_pb2.KaspadRequest(
            id=1,
            getVirtualChainFromBlockV2Request=rpc_pb2.GetVirtualChainFromBlockV2RequestMessage(
                startHash=start_hash,
                dataVerbosityLevel=rpc_pb2.FULL,
            ),
        )
    ]


def _request_iter() -> Any:
    for request in _requests():
        yield request


def _iter_requests(requests: list[Any]) -> Any:
    for request in requests:
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
                "sink": getattr(dag, "sink", ""),
                "virtual_parent_hash": (list(getattr(dag, "virtualParentHashes", [])) or [""])[0],
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


def fetch_balances_by_addresses(
    endpoint: str,
    addresses: list[str],
    timeout: float = 5.0,
) -> dict[str, Any]:
    balances: dict[str, Any] = {"ok": False, "endpoint": endpoint, "addresses": addresses}
    if not addresses:
        balances.update({"ok": True, "entries": [], "total_sompi": 0})
        return balances

    channel = grpc.insecure_channel(endpoint)
    try:
        stub = messages_pb2_grpc.RPCStub(channel)
        entries: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        for response in stub.MessageStream(_iter_requests(_balance_requests(addresses)), timeout=timeout):
            payload = response.WhichOneof("payload")
            if payload != "getBalancesByAddressesResponse":
                continue
            message = getattr(response, payload)
            error = _has_error(message)
            if error:
                errors[payload] = error
            for entry in message.entries:
                entry_error = _has_error(entry)
                item = {
                    "address": entry.address,
                    "balance_sompi": int(entry.balance),
                }
                if entry_error:
                    item["error"] = entry_error
                entries.append(item)

        total_sompi = sum(int(entry.get("balance_sompi") or 0) for entry in entries)
        balances.update(
            {
                "ok": not errors,
                "entries": entries,
                "total_sompi": total_sompi,
                "errors": errors,
            }
        )
        if errors:
            balances["detail"] = "; ".join(f"{key}: {value}" for key, value in errors.items())
        return balances
    except Exception as exc:
        balances["error"] = str(exc)
        balances["detail"] = str(exc)
        return balances
    finally:
        channel.close()


def _transaction_id(transaction: Any) -> str:
    verbose = getattr(transaction, "verboseData", None)
    return (
        getattr(verbose, "transactionId", "")
        or getattr(verbose, "hash", "")
        or ""
    )


def _output_address(output: Any) -> str:
    verbose = getattr(output, "verboseData", None)
    return getattr(verbose, "scriptPublicKeyAddress", "") if verbose is not None else ""


def _input_address(input_item: Any) -> str:
    verbose = getattr(input_item, "verboseData", None)
    utxo = getattr(verbose, "utxoEntry", None) if verbose is not None else None
    utxo_verbose = getattr(utxo, "verboseData", None) if utxo is not None else None
    return getattr(utxo_verbose, "scriptPublicKeyAddress", "") if utxo_verbose is not None else ""


def _input_amount(input_item: Any) -> int | None:
    verbose = getattr(input_item, "verboseData", None)
    utxo = getattr(verbose, "utxoEntry", None) if verbose is not None else None
    if utxo is None:
        return None
    return int(getattr(utxo, "amount", 0))


def _mempool_item(address: str, direction: str, entry: Any) -> dict[str, Any]:
    transaction = entry.transaction
    if direction == "receiving":
        amount_sompi = sum(
            int(output.amount)
            for output in transaction.outputs
            if _output_address(output) == address
        )
    else:
        input_amounts = [
            amount
            for input_item in transaction.inputs
            if _input_address(input_item) == address
            for amount in [_input_amount(input_item)]
            if amount is not None
        ]
        amount_sompi = sum(input_amounts) if input_amounts else None
    return {
        "address": address,
        "direction": direction,
        "tx_id": _transaction_id(transaction),
        "amount_sompi": amount_sompi,
        "fee_sompi": int(getattr(entry, "fee", 0)),
        "is_orphan": bool(getattr(entry, "isOrphan", False)),
        "outputs": len(transaction.outputs),
        "inputs": len(transaction.inputs),
    }


def _mempool_transaction_item(entry: Any) -> dict[str, Any]:
    transaction = entry.transaction
    outputs = [
        {
            "address": _output_address(output),
            "amount_sompi": int(getattr(output, "amount", 0)),
        }
        for output in transaction.outputs
    ]
    total_output_sompi = sum(int(output.get("amount_sompi") or 0) for output in outputs)
    largest_output_sompi = max((int(output.get("amount_sompi") or 0) for output in outputs), default=0)
    return {
        "tx_id": _transaction_id(transaction),
        "fee_sompi": int(getattr(entry, "fee", 0)),
        "is_orphan": bool(getattr(entry, "isOrphan", False)),
        "outputs": outputs,
        "output_count": len(transaction.outputs),
        "input_count": len(transaction.inputs),
        "total_output_sompi": total_output_sompi,
        "largest_output_sompi": largest_output_sompi,
    }


def _optional_transaction_id(transaction: Any) -> str:
    verbose = getattr(transaction, "verboseData", None)
    return (
        getattr(verbose, "transactionId", "")
        or getattr(verbose, "hash", "")
        or ""
    )


def _optional_output_address(output: Any) -> str:
    verbose = getattr(output, "verboseData", None)
    return getattr(verbose, "scriptPublicKeyAddress", "") if verbose is not None else ""


def _optional_output_amount(output: Any) -> int:
    return int(getattr(output, "value", 0) or getattr(output, "amount", 0) or 0)


def _accepted_transaction_item(transaction: Any, accepting_block_hash: str) -> dict[str, Any]:
    outputs = [
        {
            "address": _optional_output_address(output),
            "amount_sompi": _optional_output_amount(output),
        }
        for output in transaction.outputs
    ]
    total_output_sompi = sum(int(output.get("amount_sompi") or 0) for output in outputs)
    largest_output_sompi = max((int(output.get("amount_sompi") or 0) for output in outputs), default=0)
    return {
        "tx_id": _optional_transaction_id(transaction),
        "accepting_block_hash": accepting_block_hash,
        "outputs": outputs,
        "output_count": len(transaction.outputs),
        "input_count": len(transaction.inputs),
        "total_output_sompi": total_output_sompi,
        "largest_output_sompi": largest_output_sompi,
    }


def fetch_virtual_chain_transactions(
    endpoint: str,
    start_hash: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    chain: dict[str, Any] = {"ok": False, "endpoint": endpoint, "start_hash": start_hash}
    if not start_hash:
        chain["detail"] = "no start hash configured"
        return chain
    channel = grpc.insecure_channel(endpoint)
    try:
        stub = messages_pb2_grpc.RPCStub(channel)
        entries: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        added_hashes: list[str] = []
        removed_hashes: list[str] = []
        for response in stub.MessageStream(_iter_requests(_virtual_chain_requests(start_hash)), timeout=timeout):
            payload = response.WhichOneof("payload")
            if payload != "getVirtualChainFromBlockV2Response":
                continue
            message = getattr(response, payload)
            error = _has_error(message)
            if error:
                errors[payload] = error
            added_hashes.extend(list(getattr(message, "addedChainBlockHashes", [])))
            removed_hashes.extend(list(getattr(message, "removedChainBlockHashes", [])))
            for block_txs in message.chainBlockAcceptedTransactions:
                header = getattr(block_txs, "chainBlockHeader", None)
                accepting_block_hash = getattr(header, "hash", "") if header is not None else ""
                for transaction in block_txs.acceptedTransactions:
                    entries.append(_accepted_transaction_item(transaction, accepting_block_hash))

        chain.update(
            {
                "ok": not errors,
                "entries": entries,
                "added_chain_block_hashes": added_hashes,
                "removed_chain_block_hashes": removed_hashes,
                "errors": errors,
            }
        )
        if errors:
            chain["detail"] = "; ".join(f"{key}: {value}" for key, value in errors.items())
        return chain
    except Exception as exc:
        chain["error"] = str(exc)
        chain["detail"] = str(exc)
        return chain
    finally:
        channel.close()


def fetch_mempool_entries(
    endpoint: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    mempool: dict[str, Any] = {"ok": False, "endpoint": endpoint}
    channel = grpc.insecure_channel(endpoint)
    try:
        stub = messages_pb2_grpc.RPCStub(channel)
        entries: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        for response in stub.MessageStream(_iter_requests(_all_mempool_requests()), timeout=timeout):
            payload = response.WhichOneof("payload")
            if payload != "getMempoolEntriesResponse":
                continue
            message = getattr(response, payload)
            error = _has_error(message)
            if error:
                errors[payload] = error
            for item in message.entries:
                entries.append(_mempool_transaction_item(item))

        mempool.update({"ok": not errors, "entries": entries, "errors": errors})
        if errors:
            mempool["detail"] = "; ".join(f"{key}: {value}" for key, value in errors.items())
        return mempool
    except Exception as exc:
        mempool["error"] = str(exc)
        mempool["detail"] = str(exc)
        return mempool
    finally:
        channel.close()


def fetch_mempool_entries_by_addresses(
    endpoint: str,
    addresses: list[str],
    timeout: float = 5.0,
) -> dict[str, Any]:
    mempool: dict[str, Any] = {"ok": False, "endpoint": endpoint, "addresses": addresses}
    if not addresses:
        mempool.update({"ok": True, "entries": []})
        return mempool

    channel = grpc.insecure_channel(endpoint)
    try:
        stub = messages_pb2_grpc.RPCStub(channel)
        entries: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        for response in stub.MessageStream(_iter_requests(_mempool_requests(addresses)), timeout=timeout):
            payload = response.WhichOneof("payload")
            if payload != "getMempoolEntriesByAddressesResponse":
                continue
            message = getattr(response, payload)
            error = _has_error(message)
            if error:
                errors[payload] = error
            for address_entry in message.entries:
                address = address_entry.address
                for item in address_entry.receiving:
                    entries.append(_mempool_item(address, "receiving", item))
                for item in address_entry.sending:
                    entries.append(_mempool_item(address, "sending", item))

        mempool.update({"ok": not errors, "entries": entries, "errors": errors})
        if errors:
            mempool["detail"] = "; ".join(f"{key}: {value}" for key, value in errors.items())
        return mempool
    except Exception as exc:
        mempool["error"] = str(exc)
        mempool["detail"] = str(exc)
        return mempool
    finally:
        channel.close()
