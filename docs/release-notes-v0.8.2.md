# Kaspa Node Watchtower v0.8.2 Release Notes

Release date: 2026-06-20

## Highlights

- Added a live watch readiness check so operators can verify indexer/SDK
  watched-address coverage before waiting for a real transaction.
- Added watch readiness Prometheus metrics and the
  `KaspaWatchReadinessNotReady` alert.
- Added a watched-address event drill command for validating local state,
  generated pages, Prometheus metrics, and Discord alert formatting.
- Added KAS/USDT market positioning-risk signals from funding z-score,
  OI/volume crowding, futures basis, and cross-exchange spot dispersion.
- Added Prometheus, Grafana, generated status page, and report visibility for
  market positioning risk.
- Added a synthetic market-risk drill command for validating
  `KaspaMarketPositioningRiskHigh` and dashboard routing.

## Operator Commands

```bash
make discord-watch-check
make discord-watch-drill LABEL=drill TX_ID=v082-drill-1 AMOUNT_KAS=1.23
make market-risk-drill MARKET_RISK_SCORE=4 MARKET_RISK_REASON=funding_z_extreme MARKET_RISK_DIRECTION=long_crowded
make prometheus
open state/status.html
```

## Verification Checklist

```bash
make validate
python3 -m unittest discover -s tests
prometheus/run_rule_tests.sh
python3 -m json.tool grafana/kaspa-watchtower.json >/dev/null
make prometheus
make discord-watch-check
make discord-watch-drill LABEL=drill TX_ID=v082-drill-1 AMOUNT_KAS=1.23
make market-risk-drill MARKET_RISK_SCORE=4 MARKET_RISK_REASON=funding_z_extreme MARKET_RISK_DIRECTION=long_crowded
make integrations
make smoke
```

## Metrics And Alerts

- `kaspa_watchtower_watch_readiness_ok`
- `kaspa_watchtower_market_positioning_risk_score`
- `kaspa_watchtower_market_positioning_risk_level`
- `kaspa_watchtower_market_positioning_risk_reasons`
- `KaspaWatchReadinessNotReady`
- `KaspaMarketPositioningRiskHigh`

## Notes

The live browser Futures panel estimates partial market risk from Bybit linear
OI/volume and basis. Persisted market snapshots remain the authoritative source
for full positioning risk because they also include funding z-score and
cross-exchange spot dispersion.
