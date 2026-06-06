# Compatibility

Kaspa Node Watchtower is designed for local or private-network `kaspad`
operators. The core checks are network-agnostic, but thresholds and expectations
should match the network being watched.

## Current Baseline

- Primary tested node: `rusty-kaspa` / `kaspad` 2.0.0
- Primary tested network: mainnet
- Primary tested host: macOS with launchd
- Primary tested endpoints: local RPC/gRPC on `127.0.0.1`
- Primary tested Python: 3.12 or newer

The current local deployment context is tracked in
`docs/development-context.md`.

## gRPC API Surface

The gRPC probe uses these read-only requests:

- `GetInfo`
- `GetServerInfo`
- `GetBlockDagInfo`
- `GetConnectedPeerInfo`
- `GetMetrics`
- `GetSyncStatus`

The watchtower reads these fields when available:

- server version and network id
- sync status
- peer counts and peer user agents
- virtual DAA score
- block and header counts
- mempool size
- DAG tip and virtual parent counts
- pruning point hash
- difficulty
- process, connection, and consensus metrics

If upstream `rusty-kaspa` changes protobuf names, removes a response field, or
changes the bidirectional stream behavior, `kaspa_grpc_probe.py` and the
generated files under `generated_proto/` must be reviewed together.

## Protobuf Update Workflow

When updating `proto/rpc.proto` or `proto/messages.proto` from upstream:

```bash
make bootstrap
make proto-check
python3 -m unittest discover -s tests
make smoke
```

`make proto-check` regenerates protobuf files in a temporary directory and
compares them against the checked-in files under `generated_proto/`. It fails if
the generated files are stale.

After a protobuf refresh, verify that `kaspa_grpc_probe.py` still receives the
expected read-only responses:

```bash
.venv/bin/python watchtower.py -c config.json --summary
make integrations
```

## Network Notes

### Mainnet

Use strict production thresholds after the node is synced:

- `thresholds.require_synced=true`
- `thresholds.require_relay_progress_when_unsynced=true`
- `thresholds.require_sync_progress_when_unsynced=true`

During first bootstrap, temporarily allow unsynced progress monitoring:

- `thresholds.require_synced=false`
- `thresholds.require_relay_progress_when_unsynced=false`
- `thresholds.require_sync_progress_when_unsynced=true`

Re-enable strict synced monitoring after the sync-completed alert.

### Testnet

Use a distinct `node_name`, state path, benchmark path, status page, log path,
data directory, and recovery history path. Do not mix testnet and mainnet
history files because benchmark DAA and block deltas are not comparable across
networks.

Testnet peer count and relay expectations may be lower than mainnet. Tune:

- `thresholds.min_peer_count`
- `thresholds.min_relay_blocks_in_window`
- `thresholds.progress_window_minutes`

### Simnet

Simnet can be quiet or manually driven. Relay progress and peer expectations
may need to be disabled or set to zero:

- `thresholds.min_peer_count=0`
- `thresholds.min_relay_blocks_in_window=0`
- `thresholds.require_synced=false`

Use simnet mostly for parser, protobuf, alert, and dashboard smoke tests rather
than production liveness expectations.

### Devnet

Devnet behavior depends on how the local network is launched. Treat it like
testnet for path isolation and like simnet for threshold tuning until stable
traffic and peer behavior are known.

## Upgrade Checklist

Before upgrading `rusty-kaspa` or switching networks:

```bash
make benchmark
.venv/bin/python scripts/upgrade_checkpoint.py --phase before --label <label>
```

After the upgrade or network switch:

```bash
make validate
make summary
make smoke
make integrations
make benchmark
.venv/bin/python scripts/upgrade_checkpoint.py --phase after --label <label>
.venv/bin/python scripts/upgrade_checkpoint.py --compare --label <label>
```

Review:

- failed checks
- `grpc_metrics.network_id`
- `grpc_metrics.server_version`
- sync state
- peer counts
- DAA and block deltas
- disk delta
- relay progress
- Prometheus exporter health
- Grafana dashboard rendering

## Known Compatibility Risks

- Upstream protobuf or gRPC stream changes can break direct metrics collection.
- Very quiet networks can look unhealthy unless relay and peer thresholds are
  tuned for that network.
- Network switches require isolated state and benchmark files.
- macOS launchd recovery commands are host-specific.
- Local SQLite history is not a cross-host storage layer.
