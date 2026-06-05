# Kaspa Node Watchtower

[![smoke](https://github.com/psdjcraw/Kaspa-Node-Watchtower/actions/workflows/smoke.yml/badge.svg)](https://github.com/psdjcraw/Kaspa-Node-Watchtower/actions/workflows/smoke.yml)
[![codeql](https://github.com/psdjcraw/Kaspa-Node-Watchtower/actions/workflows/codeql.yml/badge.svg)](https://github.com/psdjcraw/Kaspa-Node-Watchtower/actions/workflows/codeql.yml)
[![License](https://img.shields.io/github/license/psdjcraw/Kaspa-Node-Watchtower)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue)

Lightweight monitoring and reporting tools for a local Kaspa node.

## Goal

Kaspa Node Watchtower watches a local `kaspad` process, summarizes sync progress,
and helps operators understand node health without relying only on external
explorers or hosted APIs.

## Why This Matters

Self-hosted Kaspa nodes are healthier when operators can inspect their own
systems directly. Hosted explorers and public APIs are useful references, but
they should not be the only source of truth for node health, sync progress, or
relay freshness.

Kaspa Node Watchtower helps operators keep local visibility over their nodes
with direct process, filesystem, log, RPC, gRPC, Prometheus, and Grafana signals.
That makes independent node operation easier to monitor, debug, and recover,
which supports a more resilient decentralized network.

## Dashboard Preview

![Kaspa Node Watchtower dashboard preview](docs/assets/dashboard-preview.svg)

## Features

- Node health checks: process, RPC TCP, gRPC metrics, disk free space, data directory, log freshness, and relay block progress
- Sync reports: IBD start/end time, processed blocks, headers, and throughput
- Alert-mode output for Discord/OpenClaw cron
- JSON output for later dashboards or exporters
- Direct rusty-kaspa gRPC metrics: sync status, peers, network id, DAA score, block/header counts, mempool, DAG tips, pruning point, difficulty, and process metrics
- Alert severity, repeat suppression, history, and local HTML status page generation
- Concise `--summary` output for quick Discord/operator status checks
- Benchmark snapshots and reports for version/configuration comparison
- Benchmark trend section in the generated status dashboard
- Prometheus textfile metrics for local scraping or textfile collectors

## Planned Features

- Push benchmark history to a long-lived metrics store

## Current Context

Current target environment:

- Kaspa mainnet
- Local `rusty-kaspa` / `kaspad`
- macOS host
- Discord-based operational updates

## Documentation

- [Install guide](docs/install.md)
- [Operations guide](docs/operations.md)
- [Prometheus/Grafana integrations](docs/integrations.md)
- [Failure handling runbook](docs/runbook.md)
- [Deployment status](docs/status.md)
- [Roadmap](ROADMAP.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

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

Print the watchtower version:

```bash
.venv/bin/python watchtower.py --version
make version
```

Validate local configuration:

```bash
.venv/bin/python watchtower.py -c config.json --validate-config
```

The reporter reads local process state, RPC TCP reachability, direct gRPC
metrics, data directory size/free space, and recent `kaspad` logs. It reports
IBD/catch-up completion counts, trusted block counts, latest relay activity,
latest transaction throughput stats, and recent relay block progress for stall
detection.

For gRPC metrics, create the local virtualenv and generated protobuf files:

```bash
make bootstrap
```

For local edits, copy `config.example.json` to `config.json` and adjust paths.
`config.json` is ignored by git.

Cron-friendly alert mode:

```bash
./run_watchtower.sh
```

Common operator commands are also available through `make`:

```bash
make help
make status
make smoke
make daily-report
make diagnostics-archive
```

Save a benchmark snapshot and compare recent snapshots:

```bash
.venv/bin/python watchtower.py -c config.json --benchmark-snapshot
.venv/bin/python watchtower.py -c config.json --benchmark-report
```

Capture upgrade checkpoints:

```bash
scripts/upgrade_checkpoint.py before --label pre-upgrade
scripts/upgrade_checkpoint.py after --label post-upgrade
scripts/upgrade_checkpoint.py report
```

Export history to SQLite:

```bash
scripts/export_history_sqlite.py
```

This imports benchmark snapshots, upgrade checkpoints, and recovery attempts.

Apply retention limits to state files:

```bash
.venv/bin/python watchtower.py -c config.json --prune-state
```

Cron-friendly benchmark snapshot mode:

```bash
./run_benchmark_snapshot.sh
```

Write Prometheus textfile metrics:

```bash
.venv/bin/python watchtower.py -c config.json --prometheus
```

Serve the metrics over HTTP:

```bash
./run_prometheus_exporter.sh
```

Endpoint:

```text
http://127.0.0.1:9660/metrics
```

Grafana dashboard JSON:

```text
grafana/kaspa-watchtower.json
```

Prometheus alert rules:

```text
prometheus/kaspa-watchtower-rules.yml
```

Run alert rule tests:

```bash
prometheus/run_rule_tests.sh
```

Run unit tests:

```bash
python3 -m unittest discover -s tests
```

Run the full local smoke test:

```bash
scripts/smoke_test.sh
```

External integration checks are separate:

```bash
make integrations
KASPA_WATCHTOWER_SMOKE_INTEGRATIONS=1 scripts/smoke_test.sh
```

Run local failure simulations without touching the live node:

```bash
scripts/simulate_failures.sh
```

Collect a local diagnostics bundle:

```bash
scripts/collect_diagnostics.sh
scripts/collect_diagnostics.sh --archive
```

GitHub Actions runs the static smoke workflow and CodeQL analysis on pushes to
`main` and pull requests.

Check the latest GitHub Actions smoke and CodeQL runs:

```bash
scripts/check_ci_status.sh
KASPA_WATCHTOWER_GITHUB_WORKFLOW=codeql.yml scripts/check_ci_status.sh
```

Check Prometheus alert state:

```bash
scripts/check_prometheus_alerts.sh
```

Cron-friendly smoke test:

```bash
./run_smoke_test.sh
```

Generate a daily operator report:

```bash
./run_daily_report.sh
make daily-report
```

See [Documentation](#documentation) for setup, integrations, operations,
security, roadmap, and release history.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
