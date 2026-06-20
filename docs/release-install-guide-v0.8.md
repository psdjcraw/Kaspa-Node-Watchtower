# v0.8 Release Install Guide

This guide is the fresh-host handoff path for Kaspa Node Watchtower v0.8 work.
Use it when setting up a new macOS node host or moving an existing checkout to
a more repeatable launchd-based operation.

## Fresh macOS Host

Clone the repository and enter the checkout:

```bash
git clone https://github.com/psdjcraw/Kaspa-Node-Watchtower.git
cd Kaspa-Node-Watchtower
```

Bootstrap Python and generated protobuf files:

```bash
make bootstrap
```

Run the guided first-pass check:

```bash
make onboard
```

Create a local config if one does not exist:

```bash
scripts/onboard_local.sh --write-config
```

Edit `config.json` for the host:

- `node_name`
- `process_match`
- `rpc_endpoint`
- `grpc_endpoint`
- `log_path`
- `data_dir`
- `recovery.restart_command`

Use a lowercase node name that includes a network hint, such as
`kaspa-mainnet-local`, `kaspa-tn10-local`, or `kaspa-mainnet-macmini`.

Validate before starting services:

```bash
make validate
make proto-check
make summary
make prometheus
```

Run a full local smoke pass before handoff:

```bash
make smoke
```

## launchd Services

Preview generated plists before applying them:

```bash
scripts/manage_launchd.sh --service exporter print
scripts/manage_launchd.sh install
```

Install or repair the managed service set:

```bash
make launchd-install
make launchd-status
```

Managed services:

- `exporter`: Prometheus HTTP exporter, kept alive
- `status`: alert-mode watchtower check every 5 minutes
- `benchmark`: benchmark snapshot every 30 minutes
- `daily`: daily operator report at 09:10
- `weekly`: weekly operator report on Monday at 09:30
- `alerts`: Prometheus alert bridge every 5 minutes
- `smoke`: daily smoke test at 03:20

Reload or remove services:

```bash
make launchd-restart
make launchd-uninstall
```

Scope manual operations to one service when needed:

```bash
scripts/manage_launchd.sh --service exporter status
scripts/manage_launchd.sh --service alerts --apply restart
```

## Multi-Node Operation

Use stable names that encode network and host role:

- `kaspa-mainnet-local`
- `kaspa-mainnet-macmini`
- `kaspa-tn10-local`
- `kaspa-devnet-lab`

Keep `sqlite_history_path` explicit in every config. Use separate state
directories per host by default. Share or copy SQLite history only when you
intentionally want a combined operator comparison window.

Check multi-node history after benchmark history exists:

```bash
make history-multi-node
scripts/export_history_sqlite.py --multi-node-summary --days 7
```

Optional comparison thresholds can be set per command:

```bash
MULTI_NODE_DAA_LAG_WARNING=120 \
MULTI_NODE_BLOCK_LAG_WARNING=120 \
MULTI_NODE_STALE_MINUTES=10 \
make history-multi-node
```

Run `make validate` after setting `MULTI_NODE_*` overrides; invalid values are
reported as `env.MULTI_NODE_*` failures.

## Prometheus and Grafana

Confirm the exporter is serving metrics:

```bash
curl -fsS http://127.0.0.1:9660/-/healthy
curl -fsS http://127.0.0.1:9660/metrics | grep kaspa_watchtower_status_ok
```

Add the scrape job from:

```text
integrations/asus-traffic-monitor/prometheus-scrape.yml
```

Copy alert rules into the Prometheus rules mount:

```bash
mkdir -p /Users/psdjc/.openclaw/workspace/asus-traffic-monitor/prometheus-rules
cp prometheus/kaspa-watchtower-rules.yml \
  /Users/psdjc/.openclaw/workspace/asus-traffic-monitor/prometheus-rules/kaspa-watchtower-rules.yml
```

Validate Prometheus rules:

```bash
prometheus/run_rule_tests.sh
```

For the local Docker stack, validate inside the Prometheus container:

```bash
cd /Users/psdjc/.openclaw/workspace/asus-traffic-monitor
docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose exec -T prometheus promtool check rules /etc/prometheus/rules/kaspa-watchtower-rules.yml
```

Install or refresh the Grafana dashboard:

```bash
cp grafana/kaspa-watchtower.json \
  /Users/psdjc/.openclaw/workspace/asus-traffic-monitor/grafana/dashboards/kaspa-watchtower.json
cd /Users/psdjc/.openclaw/workspace/asus-traffic-monitor
docker compose restart grafana
```

Run the integration check when the local stack is available:

```bash
make integrations
```

## Optional Kaspa Python SDK Probe

The SDK probe is read-only. It uses Kaspa Python SDK wRPC calls and
subscriptions for operational visibility, not wallet keys or signing APIs.

Install the SDK in a compatible Python environment:

```bash
python3.11 -m venv .venv-sdk-py311
.venv-sdk-py311/bin/python -m pip install --upgrade pip kaspa
```

If the main Watchtower virtualenv can install `kaspa`, `sdk_probe.python_bin`
can stay empty. Otherwise, point it at the SDK-specific Python:

```json
"sdk_probe": {
  "enabled": true,
  "endpoint": "127.0.0.1:17110",
  "network_id": "mainnet",
  "encoding": "borsh",
  "timeout_seconds": 5,
  "python_bin": "/path/to/.venv-sdk-py311/bin/python",
  "subscription_enabled": true,
  "subscription_duration_seconds": 5,
  "subscription_watch_addresses": [],
  "event_history_entries": 100,
  "alert_enabled": true,
  "require_ok": false
}
```

Validate the SDK path before making it part of routine monitoring:

```bash
.venv-sdk-py311/bin/python kaspa_sdk_probe.py \
  --endpoint 127.0.0.1:17110 \
  --network-id mainnet

.venv-sdk-py311/bin/python kaspa_sdk_probe.py \
  --endpoint 127.0.0.1:17110 \
  --network-id mainnet \
  --subscriptions \
  --duration 5

make prometheus
grep kaspa_watchtower_sdk state/watchtower.prom
```

SDK subscription watch targets automatically merge valid addresses from
`sdk_probe.subscription_watch_addresses`, `wallet.watch_addresses`,
`indexer_watch.watch_addresses`, and `mining.wallet_address`. Use the same
watchlist for indexer and SDK fallback monitoring where possible.

## Alert Bridge

Check Prometheus alerts directly:

```bash
scripts/check_prometheus_alerts.sh
```

Expected healthy baseline:

- command exits `0`
- no active watchtower alerts
- `state/prometheus-alert-state.json` updates or remains valid

The `alerts` LaunchAgent runs the same bridge every 5 minutes after
`make launchd-install`.

## Homebrew Formula

The draft Homebrew formula installs the stable v0.8.2 CLI archive and exposes:

```bash
kaspa-watchtower --version
kaspa-watchtower -c ./config.json --validate-config
```

Formula path:

```text
packaging/homebrew/kaspa-node-watchtower.rb
```

Validate formula syntax after edits:

```bash
ruby -c packaging/homebrew/kaspa-node-watchtower.rb
```

Use a source checkout for v0.8 handoff workflows that need `make onboard`,
`make smoke`, `scripts/manage_launchd.sh`, Prometheus/Grafana file copying, or
wrapper scripts.

## Handoff Checklist

Before handing the host to routine operation, confirm:

- `make validate` passes
- `make summary` returns the expected node and network
- `make prometheus` writes `state/watchtower.prom`
- `make smoke` passes
- `make launchd-status` shows the expected services
- exporter health endpoint returns OK
- Prometheus target is up
- Grafana dashboard loads with current node data
- optional SDK probe metrics are either disabled intentionally or returning OK
- `prometheus/run_rule_tests.sh` passes
- `scripts/check_prometheus_alerts.sh` reports no active alerts
- `make history-multi-node` works after history exists
- `scripts/ops_snapshot.sh` is clean enough for release-readiness review

## Rollback

Remove managed LaunchAgents:

```bash
make launchd-uninstall
```

Keep `config.json` and `state/` for later inspection unless the operator has
explicitly decided to discard local history.
