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
- Add optional report generation for daily or weekly operator summaries.
  Weekly report generation is available through `make weekly-report`.
- Evaluate packaging options for easier deployment on common node hosts.

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
