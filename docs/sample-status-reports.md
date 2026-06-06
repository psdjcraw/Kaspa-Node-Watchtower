# Sample Status Reports

These examples are sanitized operator-facing reports. They show the shape of
healthy, bootstrap, and critical states without exposing private host paths,
process arguments, local usernames, or full diagnostics bundles.

Use them when comparing a live `watchtower.py --summary`, `--sync-report`,
daily report, or `state/status.html` output against an expected state.

## Healthy Mainnet Node

Expected when the node is synced, peers are stable, relay activity is recent,
and no checks are failing.

```text
Kaspa watchtower: kaspa-mainnet-local status=ok severity=ok
checked_at=2026-06-06T08:40:00+09:00
network=mainnet synced=true peers=8 active_peers=8
daa_score=452831234 blocks=1239891 headers=1239891 tips=2
relay_window=10m relay_blocks=620 relay_events=320 latest_relay_age=3s
processed=tx_rate=145.20/s age=4s tx=1452 blocks=96 window=10.0s
disk_free=213.34 GiB disk_free_percent=36.2
failed_checks=none
recovery_action=none
```

Operator verdict:

```text
verdict=healthy_no_action
benchmark_ok_ratio=100.0%
benchmark_min_peer_count=7
benchmark_min_disk_free=213.34 GiB
```

Recommended action: no action. Keep benchmark snapshots, Prometheus exporter,
and daily report cron running.

## Mainnet Bootstrap In Progress

Expected during initial mainnet catch-up when RPC/gRPC works and counters are
moving, but the node has not reached `is_synced=true`.

```text
Kaspa watchtower: kaspa-mainnet-local status=ok severity=ok
checked_at=2026-06-06T02:10:00+09:00
network=mainnet synced=false peers=8 active_peers=8
daa_score=451923000 blocks=325000 headers=1180000 tips=2
relay_window=10m relay_blocks=0 relay_events=0 latest_relay_age=unknown
processed=tx_rate=unknown age=unknown tx=unknown blocks=unknown window=unknown
sync_progress=daa_delta=+10500 block_delta=+10500 header_delta=+10500 over 30.0m
failed_checks=none
recovery_action=none
```

Relevant monitoring config while catching up:

```json
{
  "require_synced": false,
  "require_relay_progress_when_unsynced": false,
  "require_sync_progress_when_unsynced": true
}
```

Recommended action: keep watching sync rates. Do not restart only because
`is_synced=false` during an expected bootstrap window.

## Critical RPC or gRPC Failure

Expected when the process is missing or the RPC/gRPC endpoint is unavailable.
The exact failed checks may vary.

```text
Kaspa watchtower: kaspa-mainnet-local status=critical severity=critical
checked_at=2026-06-06T03:15:00+09:00
network=unknown synced=unknown peers=unknown active_peers=unknown
relay_window=10m relay_blocks=0 relay_events=0 latest_relay_age=unknown
processed=tx_rate=unknown age=unknown tx=unknown blocks=unknown window=unknown
failed_checks=process,rpc_tcp,grpc_metrics
recovery_action=manual_approval_required
```

Operator verdict:

```text
verdict=critical_operator_action_required
failed_checks=process,rpc_tcp,grpc_metrics
recovery_action=manual_approval_required
```

Recommended action:

```bash
scripts/ops_snapshot.sh
.venv/bin/python watchtower.py -c config.json --validate-config
.venv/bin/python watchtower.py -c config.json --recover --dry-run
```

Only run the non-dry-run recovery command after confirming the restart is
approved.

## Stale Processed Stats Warning

Expected when the node is synced but recent processed block/transaction log
entries are missing or older than the configured freshness threshold.

```text
Kaspa watchtower: kaspa-mainnet-local status=warn severity=warn
checked_at=2026-06-06T05:10:00+09:00
network=mainnet synced=true peers=8 active_peers=8
relay_window=10m relay_blocks=610 relay_events=315 latest_relay_age=4s
processed=tx_rate=18.50/s age=240s tx=185 blocks=22 window=10.0s
failed_checks=processed_stats_freshness
recovery_action=none
```

Recommended action:

```bash
scripts/ops_snapshot.sh
grep 'Processed .* transactions' /path/to/rusty-kaspa.log | tail -40
```

Do not restart a healthy node only because processed transaction telemetry is
stale. Confirm peer, relay, and sync health first, then inspect the log parser
or tune `thresholds.stale_processed_stats_minutes` if the stale window is
expected.

## Disk Pressure Warning

Expected when the node is otherwise healthy but free disk space drops below the
configured threshold.

```text
Kaspa watchtower: kaspa-mainnet-local status=warn severity=warn
checked_at=2026-06-06T04:20:00+09:00
network=mainnet synced=true peers=8 active_peers=8
relay_window=10m relay_blocks=580 relay_events=301 latest_relay_age=5s
disk_free=18.40 GiB disk_free_percent=4.1
failed_checks=disk_space
recovery_action=manual_approval_required
```

Recommended action: inspect disk consumers, archive diagnostics if useful, and
avoid restarting `kaspad` until storage pressure is understood.
