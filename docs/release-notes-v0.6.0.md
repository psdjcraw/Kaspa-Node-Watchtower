# Kaspa Node Watchtower v0.6.0 Release Notes

Release date: TBD

v0.6.0 focuses on transaction-throughput observability and processed-stats
freshness. The watchtower now treats recent `Processed N blocks ... (N
transactions)` log entries as first-class operator telemetry across local
status pages, Prometheus, Grafana, chat summaries, diagnostics, and history
reports.

## Highlights

- Status dashboard includes live transaction-rate cards, transaction throughput
  charts, and warning state for stale processed stats.
- Prometheus metrics expose latest processed blocks, headers, transactions,
  per-second rates, timestamps, and age.
- Prometheus alerting includes `KaspaProcessedStatsStale` for synced nodes with
  stale processed-stats telemetry.
- Grafana includes processed transaction throughput and processed-stats
  freshness panels.
- `watchtower.py --summary`, diagnostics summaries, daily reports, and
  `scripts/ops_snapshot.sh` show processed transaction rate and freshness.
- SQLite history export records processed transaction rate and processed-stats
  age, including single-node and multi-node summaries.
- `config.example.json` documents `thresholds.stale_processed_stats_minutes`.
- Runbook and sample reports include stale processed-stats triage guidance.

## Operator Commands

```bash
make summary
make diagnostics-summary
scripts/ops_snapshot.sh
make history-report
make history-multi-node
```

## Verification Checklist

Before tagging v0.6.0, verify:

```bash
make version
python3 -m unittest discover -s tests
prometheus/run_rule_tests.sh
make smoke
make integrations
make package
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.

## Known Limitations

- Processed-stats freshness depends on the current `kaspad` log format.
- Very quiet or unusual network conditions may require tuning
  `thresholds.stale_processed_stats_minutes`.
- Processed transaction telemetry is operator visibility; it does not replace
  core process, RPC, gRPC, peer, relay, disk, or sync health checks.
