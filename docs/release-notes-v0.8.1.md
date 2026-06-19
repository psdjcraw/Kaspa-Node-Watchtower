# Kaspa Node Watchtower v0.8.1 Release Notes

Release date: 2026-06-19

v0.8.1 is a post-v0.8.0 operations polish release focused on making the new
watchlist pipeline usable day to day. It keeps the v0.8 SDK/indexer watch
foundation, then adds address-level balance and UTXO visibility, clearer watch
event output, and a more reliable integration check.

## Highlights

- Indexer watchlist polling now records per-address readiness, balance, UTXO
  count, transaction count, and last check timestamp.
- Prometheus exports per-address watchlist metrics for balance, UTXO count,
  transaction count, readiness, and last-check time.
- Grafana now includes `Watchlist Balance` and `Watchlist UTXO / Tx Count`
  panels.
- `status.html` Indexer tab shows balance, UTXOs, transactions, and status for
  each watched address.
- `watch-list` Discord/CLI output now includes live address state and recent
  watch events, not just configured addresses.
- Watch event alerts now use a compact one-line format with label, source,
  direction/type, amount, transaction ID, address, and observed time.
- `scripts/check_integrations.sh` avoids `pipefail` false failures from
  `curl | grep -q` checks.
- Homebrew formula on `main` was updated to the v0.8.0 release asset after the
  v0.8.0 release.

## Operator Commands

```bash
make discord-watch-list
make prometheus
make integrations
make smoke
```

## Prometheus Metrics

- `kaspa_watchtower_indexer_watch_address_ready`
- `kaspa_watchtower_indexer_watch_address_balance_sompi`
- `kaspa_watchtower_indexer_watch_address_balance_kas`
- `kaspa_watchtower_indexer_watch_address_utxos`
- `kaspa_watchtower_indexer_watch_address_transactions`
- `kaspa_watchtower_indexer_watch_address_last_check_timestamp_seconds`

## Verification Checklist

Before tagging v0.8.1, verify:

```bash
make version
make validate
python3 -m unittest discover -s tests
prometheus/run_rule_tests.sh
python3 -m json.tool grafana/kaspa-watchtower.json
make prometheus
make discord-watch-list
make integrations
make smoke
make package
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.

## Known Limitations

- Watchlist balance and UTXO state depend on the configured indexer address
  balance and UTXO endpoints.
- SDK subscription fallback still only emits UTXO events when watched addresses
  actually change on chain.
- `watch-list` now performs live indexer reads, so it can take longer than the
  old config-only output when the indexer is slow.
