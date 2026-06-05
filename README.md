# Kaspa Node Watchtower

Lightweight monitoring and reporting tools for a local Kaspa node.

## Goal

Kaspa Node Watchtower watches a local `kaspad` process, summarizes sync progress,
and helps operators understand node health without relying only on external
explorers or hosted APIs.

## Features

- Node health checks: process, RPC TCP, disk free space, data directory, log freshness, and relay block progress
- Sync reports: IBD start/end time, processed blocks, headers, and throughput
- Alert-mode output for Discord/OpenClaw cron
- JSON output for later dashboards or exporters

## Planned Features

- Peer count via a real Kaspa RPC client
- Local dashboard for recent logs and operational status
- Version-to-version sync benchmark reports

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
python3 watchtower.py -c config.example.json
```

For the current local node:

```bash
python3 watchtower.py -c config.json
```

The reporter reads local process state, RPC TCP reachability, data directory
size/free space, and recent `kaspad` logs. It reports IBD/catch-up completion
counts, trusted block counts, latest relay activity, latest transaction
throughput stats, and recent relay block progress for stall detection.

For local edits, copy `config.example.json` to `config.json` and adjust paths.
`config.json` is ignored by git.

Cron-friendly alert mode:

```bash
./run_watchtower.sh
```

See `docs/operations.md` for alert criteria and the Discord cron plan.
