# Roadmap

Kaspa Node Watchtower is a local-first operator toolkit for monitoring,
diagnosing, and reporting on self-hosted `kaspad` nodes. The roadmap focuses on
making node operation easier to inspect without depending on hosted explorers as
the primary source of truth.

## Current Focus

- Keep the core watchtower reliable for local `kaspad` health checks.
- Improve operator summaries for Discord/OpenClaw cron updates.
- Maintain Prometheus textfile metrics and Grafana dashboard coverage.
- Keep smoke tests, unit tests, and alert rule tests passing on GitHub Actions.
- Document safe diagnostics and recovery workflows for node operators.
- Use local SQLite history summaries for longer-window operator review.
- Keep the mainnet host in lightweight Watchtower mode by default, with the
  companion Rust/PostgreSQL indexer source retained but disabled until disk
  capacity, retention, and re-enable criteria are reviewed.

## Next Execution Plan

The next operator work is lightweight-first. Keep Watchtower reliable as the
standalone operator surface, finish Market Dashboard v2/Discord alert
alignment, and keep the companion indexer on long-term hold:

- Keep `make smoke`, `make validate`, and Prometheus rule tests green before
  each deployment change.
- Use `make onboard` as the first local host check before editing config or
  starting services.
- Use `scripts/manage_launchd.sh install` to preview LaunchAgent changes, then
  `make launchd-install` and `make launchd-status` on macOS hosts.
- Keep `scripts/ops_snapshot.sh` as the final release-readiness snapshot because
  it checks live node health, exporter metrics, Prometheus queries, active
  alerts, Grafana reachability, and GitHub Actions status in one command.
- Keep the local market dashboard and Discord `market-risk` output aligned so
  critical, warning, and recovered market states use the same operator language.
- Treat v0.9 indexer awareness as implemented but disabled by config on the
  lightweight mainnet host.
- Keep the manual indexer start path guarded by `CONFIRM_INDEXER_UP=1` and
  documented in `docs/lightweight-indexer-mode.md`.
- Do not resume v1.0 companion Rust indexer API validation during the current
  lightweight operating window. Revisit it only after a separate capacity and
  retention review.

## Phase Plan

### Phase 7 - Market / Discord Severity Alignment

- Keep `market`, `market-risk`, daily report, dashboard, and alert output on
  the same `state`, `severity`, `priority`, and next-action language.
- Separate market warning/critical states from node health failures so market
  crowding does not look like `kaspad` failure.
- Keep funding z-score, OI/volume, basis, and spot dispersion reasons in a
  stable order.

### Phase 8 - Release Candidate Readiness

- Keep `make smoke`, `make validate`, Prometheus rule tests, and
  `scripts/ops_snapshot.sh` green before release candidate tagging.
- Keep `CHANGELOG.md`, release notes, `README.md`, `docs/status.md`, and
  `docs/operations.md` aligned with lightweight operation.
- Confirm GitHub Actions smoke and CodeQL success for the release candidate
  commit.

### Phase 9 - 24-48 Hour Lightweight Observation

- Confirm daily reports stay quiet and continue to show Prometheus alerts as
  none.
- Confirm `kaspa_watchtower_lightweight_mode=1`, indexer enabled metrics remain
  `0`, and Docker indexer containers, images, and DB volumes do not reappear.
- Track disk free, peers, relay freshness, processed tx age, and gRPC sync
  against the current baseline.

### Phase 10 - Operator UX Polish

- Shorten daily and Discord summaries without removing health, failed checks,
  peers, relay age, disk free, and market risk.
- Keep Status UI text compact, mobile-safe, and consistent with Discord output.
- Keep SpaceX/private watchlist cards as sparse valuation candles and avoid
  intraday slots for private assets.

### Phase 11 - Long-Term Indexer Hold

- Keep `simply-kaspa-indexer` source retained but Docker containers, images,
  build cache, and PostgreSQL volumes absent by default.
- Keep `make indexer-up` guarded by `CONFIRM_INDEXER_UP=1`.
- Revisit the indexer only after disk headroom, PostgreSQL retention, DB prune
  policy, Docker cleanup, upstream/fork commit, and API scope are explicitly
  reviewed.

### Phase 12 - Watchtower-Only Roadmap

- Prioritize market risk quality, alert quality, daily/weekly reports, Grafana
  cleanup, status readability, multi-node history, and recovery/incident review.
- Move explorer/admin UI work to long-term backlog until the indexer hold is
  lifted.
- Avoid adding short-term tasks that require the PostgreSQL-backed indexer.

### Phase 18 - v0.8.3 Release Execution Prep

- Keep tag and GitHub Release publishing as separate approved operator actions.
- Prepare the release body, package/checksum, and Homebrew follow-up plan before
  tagging.
- Use `docs/release-execution-v0.8.3.md` as the execution checklist.

### Phase 19 - 24 Hour Observation

- Confirm daily report, Prometheus alerts, lightweight metrics, Docker/indexer
  resource counts, disk free, and market-risk noise stay stable.

### Phase 20 - Release Decision

- If observation is clean, decide whether to tag `v0.8.3`, upload package
  assets, and update Homebrew in a follow-up commit.

### Phase 21 - Post-Release Watch

- Watch daily report, alert bridge, Grafana, status HTML, Discord market-risk,
  and Docker/indexer resource counts for 24 hours after release.

### Phase 22 - Backlog Reset

- Keep active backlog to Watchtower-only improvements: report readability,
  Grafana lightweight panel, market-risk noise tuning, status UI polish, and
  weekly report compression.

## Long-Term Backlog - Watchtower plus Indexer

The long-term project direction is documented in
`docs/indexer-integration-plan.md`, but the current mainnet host defaults to the
lighter posture described in `docs/lightweight-indexer-mode.md`. The short
version: Python Watchtower remains the operator, alerting, reporting, and
dashboard layer; `simply-kaspa-indexer` is source-retained on long-term hold and
is available as a companion explorer layer only after a separate re-enable
review.

### v0.9 - Indexer Awareness

Status: implemented in Watchtower and disabled by config on the lightweight
mainnet host.

- Optional `indexer` configuration exists for base URL, path templates,
  metrics mode, timeout, and lag/staleness thresholds.
- Watchtower polls companion indexer health and metrics without changing the
  existing local node monitoring behavior.
- Prometheus exports indexer availability, health, metrics, lag, checkpoint
  freshness, latency, and watch-readiness signals.
- Summary/status output and alert formatting include indexer and watchlist
  state.
- Mocked tests cover healthy, stale, syncing, unavailable, lookup, and watchlist
  paths.

### Deferred v1.0 - Explorer API Baseline

- Add or consume Rust indexer endpoints for recent blocks, block details,
  transaction details, address transactions, search, and combined status.
- Add Watchtower command targets for local transaction and address lookups.
- Surface local explorer links in status output when configured.
- Validate Rust endpoints with seeded PostgreSQL fixtures and Watchtower command
  tests with mocked API responses.

### Deferred v1.1 - Watchlist and Alert Events

- Add durable watch target and watch event storage for addresses,
  transactions, blocks, large transaction rules, and indexer lag rules.
- Keep alert policy in Watchtower while using indexed data for event detection.
- Add idempotent event creation so alerts do not duplicate across restarts.

### Deferred v1.2 - Balance and UTXO Layer

- Add optional derived balance and UTXO tables behind an explicit enable flag.
- Provide balance, UTXO, and balance-event APIs for watched addresses.
- Add reconciliation checks and reorg-safe update behavior before using this
  data for operator alerts.

### Deferred v1.3+ - Admin and Explorer UI

- Build an operator-first admin UI for node health, indexer health, PostgreSQL,
  watchlists, alert history, and recent chain activity.
- Add compact local explorer pages after the API and derived data model are
  stable.

## v0.8 - Distribution and Onboarding

- Provide guided local onboarding with workspace, Python, config, state,
  launchd, optional bootstrap, and optional smoke checks.
- Manage launchd service installation, restart, status, uninstall, and plist
  rendering for exporter, status, benchmark, daily, weekly, alert bridge, and
  smoke-test jobs.
- Tighten config migration validation around SQLite history paths, multi-node
  naming, thresholds, and Prometheus/Grafana paths.
- Document the release install guide for a fresh macOS host, multi-node
  operation, Prometheus/Grafana wiring, and alert bridge verification.
- Refresh the Homebrew formula and post-install smoke guidance for the current
  release asset.

## v0.7 - Multi-Node Comparison

- Promote `make history-multi-node` from a per-node table into an operator
  comparison verdict with per-network baseline nodes, lagging nodes, risky
  nodes, latest DAA/block lag, and concise risk flags.
- Include the multi-node verdict in daily and weekly reports so scheduled
  operator reviews surface risky or lagging nodes without a separate command.
- Make DAA/block lag, stale-node, peer-lag, and processed-freshness thresholds
  configurable for stricter same-network comparisons.
- Surface the multi-node verdict in `status.html` and `scripts/ops_snapshot.sh`
  so dashboard and release-readiness checks show the same operator signal.
- Export multi-node verdict and per-node lag values as Prometheus metrics and
  add Grafana panels for the same signal.
- Alert on multi-node warning/critical verdicts, risk nodes, lagging nodes,
  stale-node flags, no-peer flags, and DAA/block lag flags.
- Use the comparison output to decide which node needs attention before adding
  broader dashboard surface area.

## 2026-06-10 Execution Status

- Continued the goal-tracked roadmap from the operator thread.
- Strengthened the observation baseline by adding an `active_peer_count`
  health check with configurable `thresholds.min_active_peer_count`.
- Classified active-peer loss as a critical health failure, separate from total
  peer count, so alerting can catch connected-but-inactive node states.
- Connected the active-peer signal to Prometheus alerting with
  `KaspaNodeActivePeerCountLow` and rule-test coverage.
- Moved the roadmap into the alert-to-recovery handoff by marking failed
  recovery commands and still-unhealthy post-recovery checks as
  `operator_required=true` in recovery history.
- Surfaced the latest `operator_required` recovery state in `status.html` so
  the dashboard shows when automatic recovery must stop and a human should
  inspect the node.
- Documented the stop-and-inspect rule for recovery attempts that do not return
  the node to healthy status.
- Added the first alert-system pass for operator-facing cause guesses, health
  score, incident duration tracking, and maintenance mute behavior.
- Exposed the new operator fields in alert/summary output, status dashboard
  facts, and Prometheus textfile metrics.
- Verified `config.example.json` validation and the watchtower unit test suite
  after the change.

## 2026-06-08 Execution Status

- Completed the single-node stability pass with live `scripts/ops_snapshot.sh`
  verification, Prometheus exporter health, zero active alerts, Grafana
  reachability, and current CI status.
- Completed failure rehearsal for peer loss, relay stalls, RPC failure, gRPC
  failure, disk pressure, stale logs, exporter downtime, repeat suppression,
  recovered transitions, and recovery dry-runs.
- Kept dashboard and alert signals aligned by emitting inactive sync-progress
  Prometheus metrics as `0` when the node is already synced.
- Built the v0.6.1 portable release package and checksum from tracked files.
- Verified multi-node SQLite history comparison with `kaspa-mainnet-local` and
  `kaspa-tn10-local` history windows.

## v0.1 - Initial Operator Toolkit

- Local process, TCP RPC, gRPC metrics, log freshness, disk, and data directory
  checks.
- Concise `--summary`, JSON output, and alert-mode reporting.
- Benchmark snapshots and trend reporting.
- Prometheus textfile metrics and local metrics HTTP serving.
- Grafana dashboard JSON and Prometheus alert rules.
- Diagnostics bundle collection and recovery-oriented runbook docs.

## v0.2 - Easier Installation and Operations

- Add a clearer installation guide for new operators.
- Publish the first tagged release with release notes and known limitations.
- Tighten configuration validation and error messages.
- Add sample sanitized status reports for common node states.
- Expand documentation for Prometheus, Grafana, and alert routing setups.
- Improve dashboard panels for sync progress, relay freshness, and recovery
  history.

## v0.3 - Broader Compatibility

- Track compatibility with current `rusty-kaspa` and `kaspad` releases.
  Initial notes are in `docs/compatibility.md`.
- Add compatibility notes for mainnet, testnet, simnet, and devnet usage.
  Initial notes are in `docs/compatibility.md`.
- Improve gRPC/protobuf update handling when upstream APIs change.
  `make proto-check` now verifies generated protobuf files are current.
- Add more failure simulations for stale logs, stalled relay blocks, missing
  metrics, disk pressure, and exporter failures.
  Exporter failure detection is now covered by `make simulate-exporter-failure`.
- Strengthen smoke tests so regressions are caught before operator deployment.

## v0.4 - Operator Automation

- Improve recovery dry-run output and decision support.
  Recovery now prints a decision block before dry-run or execution.
- Add richer diagnostics summaries for issue reports and incident review.
  Diagnostics now start with a sanitized incident summary.
- Explore external long-lived storage options beyond local SQLite.
  Candidate options are documented in `docs/storage-options.md`, and portable
  history archives are available through `make history-archive`.
- Add optional report generation for daily or weekly operator summaries.
  Weekly report generation is available through `make weekly-report`, and
  `make weekly-archive` pairs it with a portable history archive.
- Evaluate packaging options for easier deployment on common node hosts.
  Candidate options are documented in `docs/packaging-options.md`, and
  `make package` builds a portable release tarball with a manifest and checksum.

## v0.5 - Multi-Host and Distribution Follow-Up

- Draft a Homebrew formula for macOS operator installs.
  Initial draft is in `packaging/homebrew/kaspa-node-watchtower.rb`.
- Add an optional object-storage upload helper for history archives and
  diagnostics bundles.
  Archive copy/upload helper is available through `make upload-archive`.
- Explore multi-node history comparison for operators running more than one
  `kaspad` host.
  Per-node SQLite comparison is available through `make history-multi-node`.
- Add richer incident report export from sanitized diagnostics summaries.
  Markdown incident reports are available through `make incident-report`.
- Add config version checks or migration notes when defaults change.
  `config_version` validation starts at schema version `1`.

## AI-Assisted Maintenance Opportunities

OpenAI Codex and API credits would be useful for:

- Reviewing alert rules and recovery logic for edge cases.
- Summarizing diagnostics bundles into safe, sanitized issue reports.
- Generating tests from real operator failures and simulation outputs.
- Keeping runbooks, release notes, and compatibility notes current.
- Reviewing upstream `kaspad` changes that may affect gRPC metrics or node
  health interpretation.

## Non-Goals

- Replacing `kaspad` or changing consensus behavior.
- Requiring hosted APIs or external explorers for core health checks.
- Collecting wallet data, private keys, or sensitive node credentials.
- Turning the watchtower into a custodial or trading tool.
