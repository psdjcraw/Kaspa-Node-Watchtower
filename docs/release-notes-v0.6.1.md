# Kaspa Node Watchtower v0.6.1 Release Notes

Release date: 2026-06-07

v0.6.1 expands operator market visibility around KAS/USDT while keeping the
core node watchtower local-first. The status dashboard now includes richer
spot, exchange-volume, futures, and source-status views, and daily/weekly
reports can persist a compact market snapshot into the same SQLite history
workflow used for node health.

## Highlights

- Status dashboard includes daily KAS exchange-volume bars for Gate, MEXC,
  KuCoin, Bybit, Bitget, Kraken, HTX, and Total.
- Bybit KAS/USDT futures positioning now shows mark, index, basis, funding,
  annualized funding context, open interest, OI value, and 24-hour volume.
- Futures trend and estimated liquidation-map panels cover 7-day trend plus
  12-hour, 24-hour, 1-week, and 1-month liquidation ranges.
- Market Data Sources panel shows live, cached, pending, or failed public API
  groups with stable ordering and short failure details.
- Daily and weekly reports include a Bybit KAS/USDT spot/futures market
  snapshot and persist it to `state/market-snapshots.jsonl`.
- SQLite history export imports market snapshots and includes latest
  spot/futures context in `--summary` output.
- Prometheus textfile metrics and the bundled Grafana dashboard expose latest
  persisted market snapshot values for KAS/USDT spot price, futures basis, and
  futures open interest.
- Mempool activity in `status.html` is promoted to a full 10-second bar chart
  aligned with the transaction-throughput chart.
- Bundled Grafana dashboard includes a mempool size panel.

## Operator Commands

```bash
make status
.venv/bin/python watchtower.py --market-summary
.venv/bin/python watchtower.py -c config.json --market-snapshot
scripts/export_history_sqlite.py --summary --days 7
make daily-report
make weekly-report
```

## Verification Checklist

Before tagging v0.6.1, verify:

```bash
make version
python3 -m unittest discover -s tests
prometheus/run_rule_tests.sh
make smoke
make integrations
make package
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.
After the v0.6.1 release archive is uploaded, update the Homebrew formula URL
and checksum to the new release asset.

## Known Limitations

- Market dashboard panels and snapshots use public exchange APIs as reference
  context; they do not replace local node health checks.
- Liquidation maps are estimated from Bybit linear candles and open interest,
  not actual liquidation fill feeds.
- Browser-side market panels depend on public API availability and CORS
  behavior; source-status rows show cached or failed state when unavailable.
