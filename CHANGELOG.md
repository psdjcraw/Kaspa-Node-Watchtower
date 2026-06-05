# Changelog

All notable changes to Kaspa Node Watchtower are tracked here.

## Unreleased

### Added

- Benchmark stability metrics for Prometheus and the status dashboard, including
  OK ratio, severity counts, minimum peers, and minimum disk free space.
- Visual status dashboard layout with health bars, compact metric cards, and
  history sparklines for local `status.html` and canvas output.
- `make ensure-exporter` and `scripts/ensure_prometheus_exporter.sh` for
  installing, restarting, and verifying the Prometheus exporter LaunchAgent.
- `make history-report` and `scripts/export_history_sqlite.py --summary` for
  recent SQLite history summaries across benchmark, recovery, and upgrade data.
- Sanitized sample status reports for healthy, bootstrap, critical RPC/gRPC
  failure, and disk pressure states.
- Development version reporting through `watchtower.py --version` and
  `make version`.
- Bootstrap script and `make bootstrap` target for virtualenv dependency
  installation and protobuf generation.

### Changed

- Daily operator report now starts with a verdict and continues through
  integration or GitHub status failures so later sections still print, including
  a recent SQLite history summary.
- `scripts/smoke_test.sh` now keeps external integration checks optional via
  `KASPA_WATCHTOWER_SMOKE_INTEGRATIONS=1`; use `make integrations` for the
  GitHub API, Prometheus, Grafana, and exporter integration check.

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
