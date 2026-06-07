# Status

## Current Deployment

- Node: `kaspa-mainnet-local`
- Host: `hang-studio-m4max`
- Network: `mainnet`
- gRPC/RPC endpoint: `127.0.0.1:16110`
- Metrics exporter: `http://127.0.0.1:9660/metrics`
- Prometheus target: `kaspa-watchtower` at `host.docker.internal:9660`
- Grafana dashboard: `http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower`
- Grafana recovery panels: recovery counts, latest recovery timestamps, recovery trend, and recovery action mix
- Grafana sync panels: mainnet sync monitor, sync rates, sync deltas, and bootstrap progress rates
- Grafana relay panels: relay window progress and latest relay freshness
- Grafana mempool panel: mempool size timeseries from Prometheus
- GitHub Actions smoke workflow: `https://github.com/psdjcraw/Kaspa-Node-Watchtower/actions/workflows/smoke.yml`
- GitHub Actions CodeQL workflow: `https://github.com/psdjcraw/Kaspa-Node-Watchtower/actions/workflows/codeql.yml`
- Canvas status page: `/Users/psdjc/.openclaw/canvas/kaspa-watchtower/status.html`

## Local Services

- `kaspad`: matched by process name `kaspad`
- `com.openclaw.kaspa-watchtower-prometheus`: LaunchAgent serving Prometheus metrics
- Prometheus: Docker compose service in `asus-traffic-monitor`
- Grafana: Docker compose service in `asus-traffic-monitor`

## OpenClaw Cron

- `d370358a-e1f3-4456-9818-68537c558f88`:
  `kaspa-node-watchtower-alerts`, every 10 minutes
- `aef87796-2552-4cf6-b8ff-897b9ce3ca99`:
  `kaspa-watchtower-benchmark-snapshots`, every 30 minutes
- `a7e56678-da5c-43dd-8d04-0f3e6e21f1cd`:
  `kaspa-watchtower-daily-smoke-test`, daily at 03:20 KST
- `c5e0794e-f65f-420b-b07a-4918bef137ae`:
  `kaspa-watchtower-daily-operator-report`, daily at 09:10 KST
- `919e380f-9a3e-403f-b741-6241d5a60233`:
  `kaspa-watchtower-prometheus-alert-bridge`, every 5 minutes

## Files

- Config: `config.json`
- State: `state/watchtower-state.json`
- Status HTML: `state/status.html`
- Canvas HTML: `/Users/psdjc/.openclaw/canvas/kaspa-watchtower/status.html`
- Status dashboard layout: status-first operator view with incident verdict,
  dynamic health cards, trend panels, severity timeline, triage queue, check
  details, command center, relay intake chart, block processing rate chart,
  transaction throughput freshness state, mempool 10-second bar chart, live
  KAS/USDT market watch, daily exchange-volume chart, estimated futures
  liquidation maps, futures positioning and trend panels, and recovery history
- Market watch source: Bybit public spot ticker and 15-minute kline endpoints,
  plus 4-hour and daily kline endpoints, fetched by the browser when
  `status.html` is open
- Exchange-volume chart sources: Gate, MEXC, KuCoin, Bybit, Bitget, Kraken,
  and HTX public daily candles, rendered as KAS base-volume bars plus Total
- Futures liquidation maps: estimated 12-hour, 24-hour, 1-week, and 1-month
  KAS/USDT pressure zones derived from Bybit linear perp candles and open
  interest; these are not exchange-reported liquidation fills
- Futures positioning panel: Bybit KAS/USDT linear perp mark/index price,
  basis, funding, annualized funding, next funding time, open interest, OI
  value, and 24-hour futures volume
- Futures trend panel: 7-day Bybit KAS/USDT open interest line with
  funding-rate bars from public linear perp endpoints
- Market-data browser fetches: short-window panels refresh more often, while
  long-window candles, volume, funding, and liquidation-map panels are
  throttled to reduce public API load
- Market Data Sources panel: browser-side source health for spot ticker,
  timeframe candles, cross chart, exchange volume, futures positioning/trend,
  and liquidation-map groups, rendered in a stable order with pending states
  before first refresh and short error details on failures
- Benchmark JSONL: `state/benchmarks.jsonl`
- Upgrade checkpoints: `state/upgrade-checkpoints.jsonl`
- SQLite history: `state/watchtower-history.sqlite`
- Recovery history: `state/recovery-history.jsonl`
- Status dashboard recovery panel: latest recovery attempts from `state/recovery-history.jsonl`
- Prometheus recovery metrics: attempts, executed, dry-runs, skips, unavailable, and latest timestamps
- Prometheus textfile: `state/watchtower.prom`
- Last benchmark output: `state/last-benchmark-snapshot.txt`
- Last smoke output: `state/last-smoke-test.txt`
- Prometheus alert bridge state: `state/prometheus-alert-state.json`

## One-Command Snapshot

```bash
scripts/ops_snapshot.sh
```

The snapshot includes local watchtower summary output, exporter and Prometheus
processed transaction rate, processed-stats age, relay freshness, sync progress,
recovery counters, active alert names and states, Grafana reachability, and
GitHub Actions status.

## One-Command Smoke Test

```bash
scripts/smoke_test.sh
```

## CI Status

```bash
scripts/check_ci_status.sh
KASPA_WATCHTOWER_GITHUB_WORKFLOW=codeql.yml scripts/check_ci_status.sh
```

## Daily Report

```bash
./run_daily_report.sh
```

## Sample Reports

Use [sample status reports](sample-status-reports.md) to compare healthy,
bootstrap, critical RPC/gRPC failure, and disk pressure outputs against live
watchtower summaries.
