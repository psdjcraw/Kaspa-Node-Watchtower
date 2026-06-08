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

## Next Execution Plan

The next operator work should prioritize failure confidence over new dashboard
surface area:

- Keep `make smoke`, `make validate`, and Prometheus rule tests green before
  each deployment change.
- Re-run failure simulations for peer loss, relay stalls, RPC failure, gRPC
  failure, disk pressure, stale logs, exporter downtime, alert repeat
  suppression, recovered transitions, and recovery dry-runs.
- Confirm the Prometheus alert bridge can query Prometheus, update
  `state/prometheus-alert-state.json`, and report zero active alerts in the
  healthy baseline.
- Use `scripts/ops_snapshot.sh` as the final release-readiness snapshot because
  it checks live node health, exporter metrics, Prometheus queries, active
  alerts, Grafana reachability, and GitHub Actions status in one command.
- After the single-node baseline stays stable, move the next development focus
  to multi-node history comparison and operator-friendly distribution.

## v0.7 - Multi-Node Comparison

- Promote `make history-multi-node` from a per-node table into an operator
  comparison verdict with per-network baseline nodes, lagging nodes, risky
  nodes, latest DAA/block lag, and concise risk flags.
- Include the multi-node verdict in daily and weekly reports so scheduled
  operator reviews surface risky or lagging nodes without a separate command.
- Make DAA/block lag, stale-node, peer-lag, and processed-freshness thresholds
  configurable for stricter same-network comparisons.
- Use the comparison output to decide which node needs attention before adding
  broader dashboard surface area.

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
