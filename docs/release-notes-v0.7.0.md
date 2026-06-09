# Kaspa Node Watchtower v0.7.0 Release Notes

Release date: 2026-06-09

v0.7.0 turns the existing SQLite history export into a full multi-node
operator loop. The watchtower can now compare nodes by network, surface risky
or lagging nodes in scheduled reports and dashboards, export the same signal to
Prometheus/Grafana, and alert on multi-node risk conditions.

## Highlights

- `make history-multi-node` now prints an operator verdict with per-network
  baselines, lagging nodes, risky nodes, check lag, DAA/block lag, peer lag,
  processed-age lag, and concise flags.
- Mainnet and tn10 history are compared against separate baselines so
  cross-network DAA/block values are not treated as lag.
- Multi-node thresholds can be tuned with Make variables for DAA lag, block
  lag, stale checks, peer lag, and processed-age lag.
- Daily and weekly reports include multi-node history sections.
- `scripts/ops_snapshot.sh` includes multi-node history in the final
  release-readiness snapshot.
- `status.html` includes a Multi-Node History panel in the History tab.
- Prometheus metrics expose multi-node availability, verdict, risk-node count,
  lagging-node count, per-node lag values, flag counts, and individual flags.
- The bundled Grafana dashboard includes multi-node verdict, risk-node, and
  node-lag panels.
- Prometheus alert rules cover multi-node warning/critical verdicts, risk
  nodes, lagging nodes, stale-node flags, no-peer flags, and DAA/block lag.
- `config.example.json` documents `sqlite_history_path`.

## Operator Commands

```bash
make history-multi-node
make daily-report
make weekly-report
scripts/ops_snapshot.sh
python3 watchtower.py -c config.json --prometheus
prometheus/run_rule_tests.sh
```

Threshold example:

```bash
make history-multi-node \
  MULTI_NODE_DAA_LAG_WARNING=240 \
  MULTI_NODE_STALE_MINUTES=15 \
  MULTI_NODE_PEER_LAG_WARNING=3
```

## Prometheus Metrics

- `kaspa_watchtower_multi_node_available`
- `kaspa_watchtower_multi_node_verdict_value`
- `kaspa_watchtower_multi_node_nodes`
- `kaspa_watchtower_multi_node_risk_nodes`
- `kaspa_watchtower_multi_node_lagging_nodes`
- `kaspa_watchtower_multi_node_node_severity_value`
- `kaspa_watchtower_multi_node_node_ok_ratio`
- `kaspa_watchtower_multi_node_check_lag_minutes`
- `kaspa_watchtower_multi_node_daa_lag`
- `kaspa_watchtower_multi_node_block_lag`
- `kaspa_watchtower_multi_node_peer_lag`
- `kaspa_watchtower_multi_node_processed_age_lag_seconds`
- `kaspa_watchtower_multi_node_flag_count`
- `kaspa_watchtower_multi_node_flag`

## Alert Rules

- `KaspaWatchtowerMultiNodeCritical`
- `KaspaWatchtowerMultiNodeWarning`
- `KaspaWatchtowerMultiNodeRiskNodes`
- `KaspaWatchtowerMultiNodeLagging`
- `KaspaWatchtowerMultiNodeNoPeers`
- `KaspaWatchtowerMultiNodeStale`
- `KaspaWatchtowerMultiNodeChainLag`

## Verification Checklist

Before tagging v0.7.0, verify:

```bash
make version
python3 -m unittest discover -s tests
prometheus/run_rule_tests.sh
python3 -m json.tool grafana/kaspa-watchtower.json
make smoke
make package
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.

## Known Limitations

- Multi-node comparison is local SQLite history, not a cross-host database.
- Network inference currently uses node names such as `mainnet`, `tn10`, or
  `testnet`; operators should use clear node names for best results.
- Grafana panels depend on Prometheus scraping the generated textfile metrics.
