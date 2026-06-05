# Changelog

All notable changes to Kaspa Node Watchtower are tracked here.

## 0.1.0 - 2026-06-05

### Added

- Local node health checks for process state, RPC TCP, gRPC metrics, disk space,
  data directory, log freshness, and relay block progress.
- Direct rusty-kaspa gRPC metric collection for sync state, peers, network id,
  virtual DAA score, block/header counts, mempool, DAG tips, pruning point,
  difficulty, and process metrics.
- Alert severity, state transition notifications, repeat suppression, and local
  watchtower state history.
- Manual recovery command support with `--recover`, `--dry-run`, and
  `--force-recover`.
- Recovery history recording, status dashboard recovery panel, SQLite recovery
  export, Prometheus recovery metrics, Grafana recovery panels, and recovery
  failure alert rules.
- Benchmark snapshots, benchmark reports, upgrade checkpoints, retention
  pruning, and SQLite history export.
- Prometheus textfile metrics, local HTTP metrics exporter, alert rules,
  Prometheus alert bridge, and Grafana dashboard.
- HTML status dashboard and OpenClaw canvas host output.
- Diagnostics collector, failure simulation script, smoke test wrapper, daily
  operator report, Makefile command index, and GitHub Actions smoke workflow.
- Apache 2.0 license, contributing guide, issue templates, and security policy.

### Changed

- Canonical GitHub repository references now use
  `psdjcraw/Kaspa-Node-Watchtower`.
- GitHub Actions status checker follows API redirects and supports both
  `GITHUB_TOKEN` and `GH_TOKEN`.
- Daily operator report includes recovery history and SQLite history counts.

### Notes

- The local workspace path may still be
  `/Users/psdjc/.openclaw/workspace/Kaspa-Node-Watchtowe`; launchd and cron
  paths should continue to use the actual local directory path.
