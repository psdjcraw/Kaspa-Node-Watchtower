# Changelog

All notable changes to Kaspa Node Watchtower are tracked here.

## Unreleased

### Added

- gRPC network hashrate estimate collection via
  `EstimateNetworkHashesPerSecond`, with status dashboard, summary, benchmark,
  and Prometheus output.
- Status dashboard now includes a network hashrate trend chart.
- KAS/USDT timeframe charts now show short-trend badges based on current close
  position versus EMA and recent EMA slope.
- KAS/USDT timeframe charts now show RSI 14 badges for quick overbought,
  neutral, or oversold context.
- Market watch now includes a compact Signal Watch summary for EMA cross,
  RSI extreme, and above/below EMA conditions across visible timeframes.
- Status dashboard now includes a live transaction-rate card and transaction
  throughput chart derived from recent processed-stats log entries.
- Prometheus metrics and the bundled Grafana dashboard now expose latest
  processed transaction throughput.
- Processed-stats freshness is now exposed in status details and Prometheus,
  with an alert rule for stale processed transaction stats.
- Synced nodes now warn locally when processed block/transaction stats are
  stale or missing.

### Changed

- Development version now reports `0.6.0-dev` after the v0.5.0 release.
- Market watch grid panels now suppress flow-layout sibling margins so cards in
  the same grid row align at the same height.

## 0.5.0 - 2026-06-06

### Added

- Homebrew formula draft under `packaging/homebrew/` for macOS install planning.
- `scripts/upload_archive.sh` and `make upload-archive` for local, S3, or
  rclone-backed archive copy/upload flows.
- `scripts/export_history_sqlite.py --multi-node-summary` and
  `make history-multi-node` for per-node SQLite history comparison.
- `watchtower.py --incident-report` and `make incident-report` for sanitized
  Markdown incident reports.
- `config_version` validation for future config migration checks.
- Live KAS/USDT market watch in `status.html`, with Bybit spot price,
  24-hour stats, and a client-rendered 15-minute candle chart.
- KAS/USDT 15-minute candle chart now renders X-axis time labels and Y-axis
  price labels.
- Status dashboard now visualizes recent `Processed N blocks ... in the last
  Ns` log entries as a blocks-per-second chart.
- Status dashboard now stores and visualizes mempool size history.
- Status dashboard now visualizes recent relay accepted-block events as a
  relay intake chart.
- KAS/USDT market watch now includes 4-hour, daily, weekly, and monthly
  candlestick charts.
- KAS/USDT market watch now includes a normalized daily KAS/USDT vs BTC/USDT
  cross chart.
- KAS/USDT timeframe charts now include short-trend EMA overlays tuned to each
  visible range: 21EMA on 15-minute, 12EMA on 4-hour, 10EMA on daily, 13EMA on
  weekly, and 6EMA on monthly.
- KAS/USDT timeframe charts now request operator-focused ranges: 24 hours for
  15-minute, one week for 4-hour, one month for daily, one year for weekly, and
  full available history for monthly.
- Daily, weekly, and monthly market chart X-axis labels now omit time and
  collapse to day, month, and year granularity respectively.
- Intraday chart X-axis labels include both date and time where axis labels are
  rendered.
- Market data fetches now use timeout/retry handling with browser cache fallback.

### Changed

- Version now reports `0.5.0` for the v0.5.0 release.
- Generated `status.html` now uses a status-first operator dashboard layout
  with incident verdict, dynamic health cards, and responsive compact panels.
- Market watch cards now keep a consistent visual height across timeframes.
- Market cross chart now uses red for KAS/USDT and blue for BTC/USDT.
- Market cross chart status now shows the latest normalized KAS/BTC daily
  change values.
- Status dashboard trend area now includes a compact severity timeline for
  recent state changes.
- Status dashboard now includes a triage queue that surfaces failed checks with
  detail and recommended operator actions.
- Status dashboard now includes a command center with common summary,
  diagnostics, incident-report, smoke, and recovery dry-run commands.
- Command center entries now include copy buttons for local operator commands.
- README dashboard preview asset now matches the refreshed status-first
  operator dashboard.

## 0.4.0 - 2026-06-06

### Added

- Compatibility guide covering the tested `rusty-kaspa` baseline, gRPC API
  surface, network-specific threshold notes, and upgrade checklist.
- `make proto-check` and `scripts/check_generated_proto.sh` for verifying that
  checked-in generated protobuf files match `proto/*.proto`.
- `make simulate-exporter-failure` and explicit exporter health failure
  detection in integration checks.
- `make diagnostics-summary` and a sanitized incident summary at the top of
  diagnostics bundles.
- `make weekly-report` and `run_weekly_report.sh` for 7-day and 30-day operator
  history review.
- Long-lived storage and packaging option notes for v0.4 planning.
- `make history-archive` and `scripts/export_history_sqlite.py --archive-dir`
  for portable SQLite/JSONL history archives with summary JSON and manifest.
- `make weekly-archive` for weekly operator review plus a dated history archive.
- `make package` and `scripts/package_release.sh` for portable release tarballs
  with `PACKAGE-MANIFEST.json` and SHA-256 checksums.
- Explicit stalled relay block simulation coverage in `scripts/simulate_failures.sh`.

### Changed

- Version now reports `0.4.0` for the v0.4.0 release.
- GitHub smoke workflow now checks generated protobuf drift.
- Recovery dry-runs now print a decision block with failed checks, restart
  command status, and recommended next action.

## 0.2.0 - 2026-06-06

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
- Grafana panels for relay freshness, bootstrap progress rates, and recovery
  action mix.
- Failure simulations for missing gRPC metrics, disk pressure, and stale logs.
- Development version reporting through `watchtower.py --version` and
  `make version`.
- Bootstrap script and `make bootstrap` target for virtualenv dependency
  installation and protobuf generation.

### Changed

- Config validation output now includes expected value hints and a final failed
  setting summary.
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
