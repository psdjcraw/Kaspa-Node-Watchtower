# Runbook

## First Checks

Run:

```bash
scripts/ops_snapshot.sh
```

Collect diagnostics:

```bash
scripts/collect_diagnostics.sh
scripts/collect_diagnostics.sh --archive
```

Then run the full smoke test:

```bash
scripts/smoke_test.sh
```

## Watchtower Reports Critical

Check the concise status:

```bash
.venv/bin/python watchtower.py -c config.json --summary
```

Validate the local config:

```bash
.venv/bin/python watchtower.py -c config.json --validate-config
```

Open the dashboard:

```text
http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower
```

Compare the live output with sanitized examples:

```text
docs/sample-status-reports.md
```

## `process` Failed

Confirm `kaspad` is down:

```bash
ps -axo pid,pcpu,pmem,etime,command | grep kaspad
```

If restart is approved:

```bash
.venv/bin/python watchtower.py -c config.json --recover --dry-run
.venv/bin/python watchtower.py -c config.json --recover
```

## `grpc_metrics` or `rpc_tcp` Failed

Confirm the port:

```bash
nc -vz 127.0.0.1 16110
```

Check process logs:

```bash
tail -n 80 /Users/psdjc/kaspa/rusty-kaspa-mainnet-data/kaspa-mainnet/logs/rusty-kaspa.log
```

If the process is running but RPC is not responding, use manual recovery only
after confirming with Hang Hang.

## `peer_count` Failed

Check current gRPC status:

```bash
.venv/bin/python watchtower.py -c config.json --json | python3 -m json.tool
```

Look for `grpc_metrics.peer_count`, `active_peers`, and `is_synced`.

## Mainnet Initial Sync

If a newly switched mainnet node has peers and RPC/gRPC works, but
`grpc_metrics.is_synced=false`, keep bootstrap mode enabled:

```json
"require_synced": false,
"require_relay_progress_when_unsynced": false,
"require_sync_progress_when_unsynced": true
```

This prevents expected initial sync catch-up from triggering recovery alerts.
The watchtower still warns if saved DAA, block, and header counters do not move
for the configured `sync_progress_stall_minutes` window.
After the node reaches `is_synced=true`, re-enable `require_synced=true` for
strict production monitoring if needed.

When the watchtower announces sync completion, update `config.json`:

```json
"require_synced": true
```

Then run:

```bash
.venv/bin/python watchtower.py -c config.json --validate-config
./run_watchtower.sh
```

## `block_progress` Failed

Check recent relay activity:

```bash
grep ' via relay' /Users/psdjc/kaspa/rusty-kaspa-mainnet-data/kaspa-mainnet/logs/rusty-kaspa.log | tail -40
```

Check benchmark trend:

```bash
.venv/bin/python watchtower.py -c config.json --benchmark-report --benchmark-limit 48
```

## Prometheus Target Down

Check exporter:

```bash
curl -fsS http://127.0.0.1:9660/-/healthy
curl -fsS http://127.0.0.1:9660/metrics | head
```

Check LaunchAgent:

```bash
launchctl print gui/$(id -u)/com.openclaw.kaspa-watchtower-prometheus
```

Restart exporter:

```bash
make ensure-exporter
```

## Prometheus Rules Missing

Validate config:

```bash
cd /Users/psdjc/.openclaw/workspace/asus-traffic-monitor
docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose exec -T prometheus promtool check rules /etc/prometheus/rules/kaspa-watchtower-rules.yml
```

Check API:

```bash
curl -fsS http://127.0.0.1:9090/api/v1/rules
```

## Grafana Dashboard Has No Data

Check Prometheus query:

```bash
curl -fsG 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=kaspa_watchtower_status_ok'
```

If the query has data but Grafana does not, restart Grafana provisioning:

```bash
cd /Users/psdjc/.openclaw/workspace/asus-traffic-monitor
docker compose restart grafana
```

## Discord Cron Is Silent

Silent is expected when status is healthy.

Check smoke output:

```bash
tail -n 40 state/last-smoke-test.txt
```

Check benchmark output:

```bash
tail -n 40 state/last-benchmark-snapshot.txt
```

## rusty-kaspa Upgrade

Before changing the node binary or config:

```bash
scripts/smoke_test.sh
scripts/upgrade_checkpoint.py before --label pre-upgrade
```

After the node is back:

```bash
scripts/smoke_test.sh
scripts/upgrade_checkpoint.py after --label post-upgrade
scripts/upgrade_checkpoint.py report
```

Review status/severity, failed checks, DAA/block deltas, peer delta, resource
delta, and disk-free delta before considering the upgrade complete.
