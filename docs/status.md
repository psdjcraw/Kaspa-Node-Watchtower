# Status

## Current Deployment

- Node: `kaspa-tn10-local`
- Host: `hang-studio-m4max`
- Network: `testnet-10`
- gRPC/RPC endpoint: `127.0.0.1:16210`
- Metrics exporter: `http://127.0.0.1:9660/metrics`
- Prometheus target: `kaspa-watchtower` at `host.docker.internal:9660`
- Grafana dashboard: `http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower`
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

## Files

- Config: `config.json`
- State: `state/watchtower-state.json`
- Status HTML: `state/status.html`
- Canvas HTML: `/Users/psdjc/.openclaw/canvas/kaspa-watchtower/status.html`
- Benchmark JSONL: `state/benchmarks.jsonl`
- Prometheus textfile: `state/watchtower.prom`
- Last benchmark output: `state/last-benchmark-snapshot.txt`
- Last smoke output: `state/last-smoke-test.txt`

## One-Command Snapshot

```bash
scripts/ops_snapshot.sh
```

## One-Command Smoke Test

```bash
scripts/smoke_test.sh
```
