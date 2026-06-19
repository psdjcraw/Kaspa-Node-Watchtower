# Kaspa Node Watchtower v0.8.0 Release Notes

Release date: TBD

v0.8.0 turns the watchtower into a fuller handoff-ready operations bundle. It
adds guided onboarding, managed launchd services, a stricter host validation
path, hardened Prometheus alert bridging, a combined trading operations
dashboard, and optional Kaspa Python SDK monitoring for read-only wRPC and live
subscription coverage.

## Highlights

- `make onboard` provides a guided first-pass local setup check for workspace,
  Python, config, state, launchd hints, bootstrap, and smoke validation.
- `make launchd-status`, `make launchd-install`, `make launchd-restart`, and
  `make launchd-uninstall` manage the exporter, status check, benchmark, daily
  report, weekly report, Prometheus alert bridge, and smoke-test LaunchAgents.
- `watchtower.py --validate-config` checks v0.8 handoff settings such as
  SQLite history, node naming, distinct state/history/metrics paths, bundled
  Prometheus/Grafana files, and optional multi-node threshold overrides.
- The Prometheus alert bridge dedupes active alerts, emits resolved-only
  updates when alerts clear partially, sends a one-shot recovered message when
  all alerts resolve, and recovers from corrupted bridge state.
- `Kaspa Trading Ops` combines Watchtower health with Coinone BTC/KAS trading
  context, position usage, RSI entry blocks, cancel/error counters, and alert
  freshness.
- Optional Kaspa Python SDK probe metrics expose read-only wRPC availability,
  latency, peer count, sync status, virtual DAA score, block/header counts, and
  tip count.
- Optional SDK subscriptions collect short live samples for block-added,
  virtual-chain, virtual-DAA, and UTXO watch events.
- SDK UTXO watch events are deduped into Watchtower state, exported as
  persisted event metrics, and can trigger Discord alert output.
- SDK watch targets automatically merge addresses from SDK config, wallet
  watchlist, indexer watchlist, and mining payout address.
- Grafana panels show SDK RPC health, subscription event freshness, persisted
  watch events, UTXO fallback counts, and indexer-vs-SDK watch-source coverage.

## Operator Commands

```bash
make onboard
make validate
make launchd-status
make launchd-install
make integrations
make smoke
scripts/check_prometheus_alerts.sh
```

SDK probe one-shot examples:

```bash
python3 kaspa_sdk_probe.py --endpoint 127.0.0.1:17110 --network-id mainnet
python3 kaspa_sdk_probe.py --endpoint 127.0.0.1:17110 --network-id mainnet --subscriptions --duration 5
python3 watchtower.py -c config.json --prometheus
```

RC package example:

```bash
scripts/package_release.sh --dist-dir dist --label v0.8.0-rc1
```

## SDK Configuration

Minimal read-only SDK probe:

```json
"sdk_probe": {
  "enabled": true,
  "endpoint": "127.0.0.1:17110",
  "network_id": "mainnet",
  "encoding": "borsh",
  "timeout_seconds": 5,
  "python_bin": "",
  "subscription_enabled": false,
  "subscription_duration_seconds": 5,
  "subscription_watch_addresses": [],
  "event_history_entries": 100,
  "alert_enabled": true,
  "require_ok": false
}
```

Use `sdk_probe.python_bin` when the main Watchtower Python cannot install a
compatible `kaspa` wheel. The probe is read-only and does not use wallet keys
or transaction signing APIs.

## Prometheus Metrics

- `kaspa_watchtower_sdk_enabled`
- `kaspa_watchtower_sdk_installed`
- `kaspa_watchtower_sdk_rpc_up`
- `kaspa_watchtower_sdk_connected`
- `kaspa_watchtower_sdk_connect_latency_ms`
- `kaspa_watchtower_sdk_rpc_latency_ms`
- `kaspa_watchtower_sdk_peer_count`
- `kaspa_watchtower_sdk_synced`
- `kaspa_watchtower_sdk_virtual_daa_score`
- `kaspa_watchtower_sdk_block_count`
- `kaspa_watchtower_sdk_header_count`
- `kaspa_watchtower_sdk_tip_count`
- `kaspa_watchtower_sdk_subscription_enabled`
- `kaspa_watchtower_sdk_subscription_ok`
- `kaspa_watchtower_sdk_subscription_events_total`
- `kaspa_watchtower_sdk_subscription_last_event_age_seconds`
- `kaspa_watchtower_sdk_subscription_block_added_total`
- `kaspa_watchtower_sdk_subscription_virtual_chain_changed_total`
- `kaspa_watchtower_sdk_subscription_virtual_daa_score_changed_total`
- `kaspa_watchtower_sdk_subscription_watch_addresses`
- `kaspa_watchtower_sdk_subscription_utxos_changed_total`
- `kaspa_watchtower_sdk_event_history_total`
- `kaspa_watchtower_sdk_new_events`
- `kaspa_watchtower_watch_source_addresses`
- `kaspa_watchtower_watch_source_events_total`
- `kaspa_watchtower_watch_source_new_events`

## Alert Rules

- `KaspaSdkSubscriptionUnavailable`
- `KaspaSdkSubscriptionNoEvents`

## Verification Checklist

Before tagging v0.8.0, verify:

```bash
make version
make validate
python3 -m unittest discover -s tests
prometheus/run_rule_tests.sh
python3 -m json.tool grafana/kaspa-watchtower.json
make smoke
scripts/package_release.sh --dist-dir dist --label v0.8.0-rc1
tar -tzf dist/kaspa-node-watchtower-v0.8.0-rc1.tar.gz >/dev/null
shasum -a 256 -c dist/kaspa-node-watchtower-v0.8.0-rc1.tar.gz.sha256
```

When the local stack is available, also verify:

```bash
make integrations
scripts/check_prometheus_alerts.sh
curl -fsS http://127.0.0.1:9660/metrics | grep kaspa_watchtower_sdk
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.

## Known Limitations

- SDK support is optional and depends on a compatible `kaspa` Python wheel.
- CPython versions newer than the available SDK wheel may need a separate
  Python, configured through `sdk_probe.python_bin`.
- SDK subscription sampling is intentionally short-lived per Watchtower run; it
  is an operations signal, not a full archival indexer.
- UTXO watch fallback only observes configured addresses and does not replace
  the indexer for historical address or transaction queries.
