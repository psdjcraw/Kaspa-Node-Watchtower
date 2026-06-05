# Kaspa Node Watchtower

Lightweight monitoring and reporting tools for a local Kaspa node.

## Goal

Kaspa Node Watchtower watches a local `kaspad` process, summarizes sync progress,
and helps operators understand node health without relying only on external
explorers or hosted APIs.

## Features

- Node health checks: process, RPC TCP, gRPC metrics, disk free space, data directory, log freshness, and relay block progress
- Sync reports: IBD start/end time, processed blocks, headers, and throughput
- Alert-mode output for Discord/OpenClaw cron
- JSON output for later dashboards or exporters
- Direct rusty-kaspa gRPC metrics: sync status, peer count, network id, DAA score, block/header counts, process metrics
- Alert severity, repeat suppression, history, and local HTML status page generation
- Concise `--summary` output for quick Discord/operator status checks
- Benchmark snapshots and reports for version/configuration comparison

## Planned Features

- Export benchmark data to Prometheus or a long-lived metrics store

## Current Context

Initial target environment:

- Kaspa testnet 10
- Local `rusty-kaspa` / `kaspad`
- macOS host
- Discord-based operational updates

## Status

First working local watchtower.

## Quick Start

Run the local status reporter:

```bash
.venv/bin/python watchtower.py -c config.example.json
```

For the current local node:

```bash
.venv/bin/python watchtower.py -c config.json
```

For a concise operator summary:

```bash
.venv/bin/python watchtower.py -c config.json --summary
```

The reporter reads local process state, RPC TCP reachability, direct gRPC
metrics, data directory size/free space, and recent `kaspad` logs. It reports
IBD/catch-up completion counts, trusted block counts, latest relay activity,
latest transaction throughput stats, and recent relay block progress for stall
detection.

For gRPC metrics, create the local virtualenv and generated protobuf files:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m grpc_tools.protoc -I proto --python_out=generated_proto --grpc_python_out=generated_proto proto/rpc.proto proto/messages.proto
```

For local edits, copy `config.example.json` to `config.json` and adjust paths.
`config.json` is ignored by git.

Cron-friendly alert mode:

```bash
./run_watchtower.sh
```

Save a benchmark snapshot and compare recent snapshots:

```bash
.venv/bin/python watchtower.py -c config.json --benchmark-snapshot
.venv/bin/python watchtower.py -c config.json --benchmark-report
```

Cron-friendly benchmark snapshot mode:

```bash
./run_benchmark_snapshot.sh
```

See `docs/operations.md` for alert criteria and the Discord cron plan.
