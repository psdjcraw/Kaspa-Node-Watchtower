# Changelog

All notable changes to Kaspa Node Watchtower are tracked here.

## Unreleased

### Added

- Discord command bridge now exposes `market`, `market-risk`, and
  `market-drill` outputs for KAS/USDT market snapshots and positioning-risk
  drills.
- Unified operator timeline output now merges node, incident, recovery, market,
  wallet, indexer watch, SDK watch, whale, and mining events for CLI, Discord,
  and the generated status page.
- Market risk history now computes a 24-hour trend verdict, max/average score,
  event counts, active risk duration, and top reasons for Discord, Prometheus,
  SQLite history summaries, and the generated status page.
- Market indicator cards now include a wider technical-analysis set across
  visible KAS/USDT timeframes: EMA/SMA, MACD, Bollinger, Donchian, ATR, ADX,
  stochastic, CCI, Williams %R, ROC, momentum, OBV, MFI, VWAP, volume spike,
  RSI, and BTC-relative strength.
- Generated market dashboards now summarize active indicator anomalies by
  timeframe and indicator with watch, warning, and critical severity.
- Market dashboards now include KAS/USDT microstructure signals from Bybit
  orderbook and recent trades: spread, 0.5% depth, book imbalance, wall ratio,
  estimated $10k slippage, taker buy ratio, CVD, and trade-flow imbalance.
- Generated status pages now include a Toccata readiness tab with activation
  DAA tracking, server version, hardware checks, backup reminders, and
  post-Toccata RPC/indexer compatibility watchpoints.
- Indexer `/api/metrics` polling now normalizes post-Toccata schema capability
  signals for tx v1, `storageMass`, `computeBudget`, covenant bindings, UTXO
  covenant IDs, user lanes, gas, reward info, and lane-proof support.
- Indexer status pages and Prometheus output now include a post-Toccata
  fee/mass monitor for relay fee, tx v1 activity, covenant outputs, user lanes,
  gas, storage/compute/transient mass, and low-fee rejections.
- Indexer status pages and Prometheus output now include post-Toccata activity
  counters for tx v1, block v2, covenant inputs/outputs/UTXOs/IDs, user lanes,
  SeqCommit blocks, and ZK precompile usage.
- Added `docs/indexer-api-spec.md` to pin the Watchtower-facing indexer
  `/api/health` and `/api/metrics` contract for Toccata schema, fee/mass, and
  activity fields.
- Added a Covenant Explorer baseline to the Indexer tab and Prometheus output,
  covering top covenant IDs, tx/UTXO/input/output counts, token-like and
  NFT-like heuristics, and latest transaction IDs when exposed by the indexer.
- Added a Lane / SeqCommit Monitor baseline to the Indexer tab and Prometheus
  output, covering active lanes, lane tx/gas, SeqCommit block counts, lane proof
  failures, and top lane activity when exposed by the indexer.
- Added a ZK / Bridge Watch baseline to the Indexer tab and Prometheus output,
  covering proof-type activity, ZK failures, bridge-lockbox-like candidates,
  locked amounts, unlock counts, and latest transaction IDs.
- Added post-Toccata failed checks for relay fee policy drift, low-fee
  rejections, lane proof failures, and ZK proof failures from indexer metrics.
- Toccata indexer monitoring now documents and test-covers schema v24
  `rollupUpdatedAt` freshness for dashboards, daily reports, Prometheus, and
  stale-rollup alerts.
- gRPC probe output now includes peer IDs, peer ping min/p95, and structured
  error counts/types, while the Prometheus file server exports its uptime,
  metrics file age, and metrics file size.
- SNS snapshots now include a compact mood summary derived from recent YouTube/X
  items, and the local SNS page renders that summary above the item grid.
- Daily reports now use a compact Korean operator summary with node, incident,
  benchmark, sync, whale, market, SQLite history, multi-node, integration, and
  CI sections, plus an alarm wrapper that writes and summarizes the latest log.
- `watchtower.py --alert` now emits a market-risk-high alert body when a new
  critical market positioning snapshot is detected.

### Changed

- Discord `market` and `market-risk` output now includes the same dashboard
  state language used by the market dashboard (`OK`, `WARN`, `CRIT`, and
  risk-first priority) so chat alerts and the dashboard agree on severity.
- Homebrew formula now points to the v0.8.2 GitHub Release asset and checksum.

## 0.8.2 - 2026-06-20

### Added

- `make discord-watch-check` and the Discord watch bridge now provide a live
  readiness check for indexer/SDK watched-address coverage before waiting for a
  real transaction.
- Prometheus now exposes watch readiness metrics and includes
  `KaspaWatchReadinessNotReady` for not-ready watch paths.
- `make discord-watch-drill` can inject a deduped synthetic watched-address
  event to verify local state, generated pages, Prometheus metrics, and Discord
  alert formatting.
- Market snapshots now persist a KAS/USDT positioning-risk score using funding
  z-score, OI/volume crowding, futures basis, and cross-exchange spot
  dispersion.
- Prometheus, Grafana, generated status pages, and daily/weekly reports now
  surface market positioning risk, including `KaspaMarketPositioningRiskHigh`.
- `make market-risk-drill` can inject a synthetic market risk snapshot for
  alert and dashboard validation.
- `docs/release-notes-v0.8.2.md` documents the v0.8.2 operator verification
  plan.

### Changed

- Version now reports `0.8.2` for the v0.8.2 release.

## 0.8.1 - 2026-06-19

### Added

- Indexer watchlist polling now records per-address readiness, balance, UTXO
  count, transaction count, and last check timestamp.
- Prometheus metrics now expose per-address watchlist balance, UTXO count,
  transaction count, readiness, and last-check timestamp.
- Bundled Grafana dashboard now includes watchlist balance and UTXO/transaction
  count panels.
- Generated `status.html` now shows watchlist balance, UTXO count, transaction
  count, and status in the Indexer tab.

### Changed

- Homebrew formula now points to the v0.8.0 GitHub Release asset and checksum.
- `watch-list` Discord/CLI output now includes live watchlist address state,
  recent events, balance, UTXO count, transaction count, readiness, and last
  check time.
- Watchlist event alerts now use a clearer one-line format with label, source,
  direction/type, amount, transaction ID, address, and observed time.
- `scripts/check_integrations.sh` no longer false-fails under `pipefail` when
  `grep -q` exits early after finding expected `curl` output.
- Version now reports `0.8.1` for the v0.8.1 release.

## 0.8.0 - 2026-06-19

### Added

- Optional Kaspa Python SDK wRPC probe metrics can report SDK availability,
  RPC connectivity, latency, peer count, sync state, virtual DAA score, block
  count, header count, and tip count without using wallet keys or signing APIs.
- Optional SDK subscription sampling can collect live block-added,
  virtual-chain, virtual-DAA, and UTXO-watch events, with Prometheus metrics,
  alert rules, and Grafana panels for subscription freshness and event counts.
- SDK UTXO watch events are persisted and deduped in Watchtower state, exposed
  through `kaspa_watchtower_sdk_event_*` metrics, and can emit Discord alert
  output when `sdk_probe.alert_enabled` is true.
- SDK subscription watch targets now merge valid addresses from
  `sdk_probe.subscription_watch_addresses`, `wallet.watch_addresses`,
  `indexer_watch.watch_addresses`, and `mining.wallet_address`, with
  `kaspa_watchtower_watch_source_*` metrics comparing indexer, SDK, and shared
  watch coverage.
- `docs/release-notes-v0.8.0.md` documents the v0.8.0 release candidate scope,
  operator commands, SDK metrics, alert rules, verification checklist, and known
  limitations.
- `scripts/onboard_local.sh` and `make onboard` provide guided local onboarding
  checks for workspace, Python, config, state, launchd hints, optional
  bootstrap, and optional smoke validation.
- `scripts/manage_launchd.sh` and `make launchd-status`, `make
  launchd-install`, `make launchd-restart`, and `make launchd-uninstall`
  manage the exporter, status check, benchmark snapshot, daily report, weekly
  report, Prometheus alert bridge, and smoke-test LaunchAgents from the current
  checkout path.
- `watchtower.py --validate-config` now checks v0.8 migration and handoff
  settings, including `sqlite_history_path`, node-name format and network
  hints, distinct state/history/metrics paths, bundled Prometheus/Grafana
  files, and optional `MULTI_NODE_*` threshold overrides.
- `docs/release-install-guide-v0.8.md` documents the fresh macOS host
  install, launchd services, multi-node naming/history checks,
  Prometheus/Grafana setup, SDK probe setup, alert bridge verification, and
  handoff checklist.
- Homebrew formula includes post-install guidance for CLI validation plus
  source-checkout smoke, launchd, Prometheus, and Grafana workflows.

### Changed

- Version now reports `0.8.0` for the v0.8.0 release.

## 0.7.0 - 2026-06-09

### Added

- `make history-multi-node` now prints an operator verdict with per-network
  baselines, lagging nodes, risky nodes, latest DAA/block lag, check lag,
  peer lag, processed-age lag, and concise risk flags.
- Multi-node comparison thresholds can be tuned with
  `MULTI_NODE_DAA_LAG_WARNING`, `MULTI_NODE_BLOCK_LAG_WARNING`,
  `MULTI_NODE_STALE_MINUTES`, `MULTI_NODE_PEER_LAG_WARNING`, and
  `MULTI_NODE_PROCESSED_AGE_LAG_WARNING`.
- Daily and weekly operator reports now include multi-node comparison sections.
- `scripts/ops_snapshot.sh` now includes the multi-node history verdict in the
  release-readiness snapshot.
- Generated `status.html` now includes a Multi-Node History panel in the
  History tab.
- Prometheus textfile metrics now expose multi-node availability, verdict,
  risk-node counts, lagging-node counts, per-node lag values, flag counts, and
  individual risk flags.
- Bundled Grafana dashboard now includes multi-node verdict, risk-node, and
  node-lag panels.
- Prometheus alert rules and rule tests now cover multi-node warning/critical
  verdicts, risk nodes, lagging nodes, stale nodes, no-peer flags, and
  DAA/block lag flags.
- `config.example.json` now documents `sqlite_history_path`.

### Changed

- Version now reports `0.7.0` for the v0.7.0 release.
- Homebrew formula now points to the v0.6.1 release tarball and checksum.
- Generated `status.html` now separates dense operator data into Market,
  Futures, Network, Ops, and History tabs, with timeframe and liquidation-map
  selectors showing one chart range at a time.
- Added a fixed 1920x1080 `stream.html` view for OBS/YouTube broadcasts that
  rotates every 5 seconds through Overall, Network, Throughput, Mempool,
  Market, and Futures scenes.
- Generated `status.html` now links to `stream.html` from the top-right header
  controls.
- Generated `stream.html` now uses smaller broadcast typography and denser
  metric cards so each scene can show more operator context.

## 0.6.1 - 2026-06-07

### Changed

- Benchmark snapshots now persist processed transaction rate and
  processed-stats age for SQLite history summaries.
- Bundled Grafana dashboard now includes a mempool size chart.
- Generated `status.html` now renders mempool activity as 10-second bar
  buckets aligned with the transaction throughput chart.
- Generated `status.html` now includes a daily KAS exchange-volume bar chart
  for Gate, MEXC, KuCoin, Bybit, Bitget, Kraken, HTX, and Total.
- Generated `status.html` now includes estimated KAS/USDT futures liquidation
  heatmaps for 12-hour, 24-hour, 1-week, and 1-month ranges using Bybit linear
  perp candles and open interest.
- Generated `status.html` now includes a Bybit KAS/USDT linear perp
  positioning panel with mark price, funding, next funding time, open interest,
  OI value, and 24-hour futures volume.
- Generated `status.html` now includes a 7-day Bybit KAS/USDT futures trend
  panel that plots open interest with funding-rate bars.
- The futures positioning panel now includes index price, mark/index basis,
  and annualized funding-rate context.
- Browser market-data refreshes are now throttled per panel so long-window
  candles, exchange volume, funding, and liquidation maps do not all refetch
  every 30 seconds.
- Generated `status.html` now includes a Market Data Sources panel that shows
  live, cached, or failed status for public market-data API groups.
- Market Data Sources rows now render in a stable order with pending states
  before the first browser refresh completes.
- Market Data Sources failure rows now include short browser-side error details
  such as HTTP, API, or timeout messages.
- Daily and weekly operator reports now include an optional Bybit KAS/USDT
  market snapshot with spot price, 24-hour volume, futures basis, funding, and
  open-interest context.
- Market snapshots can now be persisted to `state/market-snapshots.jsonl` and
  imported into SQLite history summaries for latest spot/futures context.
- Prometheus textfile metrics and the bundled Grafana dashboard now expose
  latest persisted KAS/USDT spot price, futures basis, and futures open
  interest from market snapshots.

## 0.6.0 - 2026-06-07

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
- Bundled Grafana dashboard now includes a processed-stats freshness panel.
- Status dashboard Tx Rate card now reflects stale processed-stats warnings
  instead of staying visually neutral.
- `scripts/ops_snapshot.sh` now includes latest processed transaction rate and
  processed-stats age from both exporter and Prometheus views.
- `watchtower.py --summary` and diagnostics summaries now include latest
  processed transaction rate and processed-stats age for chat and daily reports.
- `config.example.json` now documents `thresholds.stale_processed_stats_minutes`
  so fresh installs can tune processed-stats freshness checks explicitly.
- Processed-stats freshness warnings now include threshold and operator action
  detail.
- Prometheus processed-stats stale alerts now include the observed age and
  runbook hint in annotations.
- `scripts/ops_snapshot.sh` now lists active watchtower alert names and states,
  not only the alert count.
- SQLite history export now stores and summarizes processed transaction rate and
  processed-stats age for single-node and multi-node history reports.
- Runbook, sample status reports, and v0.6.0 release notes now cover stale
  processed-stats operations.

### Changed

- Version now reports `0.6.0` for the v0.6.0 release.
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
