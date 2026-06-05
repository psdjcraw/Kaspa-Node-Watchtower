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
- Direct rusty-kaspa gRPC metrics: sync status, peers, network id, DAA score, block/header counts, mempool, DAG tips, pruning point, difficulty, and process metrics
- Alert severity, repeat suppression, history, and local HTML status page generation
- Concise `--summary` output for quick Discord/operator status checks
- Benchmark snapshots and reports for version/configuration comparison
- Benchmark trend section in the generated status dashboard
- Prometheus textfile metrics for local scraping or textfile collectors

## Planned Features

- Push benchmark history to a long-lived metrics store

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

Run local failure simulations without touching the live node:

```bash
scripts/simulate_failures.sh
```

Collect a local diagnostics bundle:

```bash
scripts/collect_diagnostics.sh
scripts/collect_diagnostics.sh --archive
```

GitHub Actions runs the static smoke workflow in `.github/workflows/smoke.yml`
on pushes to `main` and pull requests.

Check the latest GitHub Actions smoke run:

```bash
scripts/check_ci_status.sh
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

See `docs/operations.md` for alert criteria and the Discord cron plan.
See `docs/integrations.md` for Prometheus/Grafana integration steps.
See `docs/status.md` for the active deployment map.
See `docs/runbook.md` for failure handling.
See `SECURITY.md` for sensitive data handling and security reporting.
See `CHANGELOG.md` for notable project changes.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
