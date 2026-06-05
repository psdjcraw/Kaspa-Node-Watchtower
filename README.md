# Kaspa Node Watchtower

Lightweight monitoring and reporting tools for a local Kaspa node.

## Goal

Kaspa Node Watchtower watches a local `kaspad` process, summarizes sync progress,
and helps operators understand node health without relying only on external
explorers or hosted APIs.

## Planned Features

- Node health checks: process, RPC, peers, disk, CPU, and memory
- Sync reports: IBD start/end time, processed blocks, headers, and throughput
- Discord alerts for stalled sync, peer loss, disk growth, or RPC failure
- Local dashboard for recent logs and operational status
- Version-to-version sync benchmark reports

## Current Context

Initial target environment:

- Kaspa testnet 10
- Local `rusty-kaspa` / `kaspad`
- macOS host
- Discord-based operational updates

## Status

Early development.
