# Kaspa Node Watchtower v0.8.3 Release Notes

v0.8.3 is a lightweight-operation stabilization release. It keeps the Python
Watchtower as the active operator surface while the companion PostgreSQL-backed
indexer remains source-retained and disabled by default.

## Highlights

- Lightweight mode is explicit in daily reports, Prometheus metrics, status
  HTML, and `scripts/ops_snapshot.sh`.
- Daily reports include Prometheus watchtower alert count and Docker indexer
  recreation counts.
- Status HTML shows a `Lightweight mode` badge and an Indexer tab notice for the
  expected healthy disabled-indexer posture.
- Market snapshot, daily report, Discord market, Discord market-risk, and alert
  output now share the same operator `state`, `severity`, `priority`, and
  next-action language.
- SpaceX watchlist cards render sparse private valuation marks as 1D, 1W, and
  1M candlesticks.
- `ROADMAP.md` is reorganized around Watchtower-only lightweight operation, with
  indexer-backed explorer/admin work moved to long-term backlog.
- `watchtower.py --version` reports `0.8.3`.

## Release Scope

- The v0.8.3 package is generated from tracked files only with `make package`.
- The Homebrew formula is updated after the GitHub Release asset exists and its
  checksum is known.
- Release execution steps are tracked in
  `docs/release-execution-v0.8.3.md`.

## Verification Checklist

Before tagging v0.8.3, verify:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile watchtower.py tests/test_watchtower.py
bash -n run_daily_report.sh scripts/ops_snapshot.sh
python3 watchtower.py -c config.json --summary
./run_daily_report.sh
scripts/ops_snapshot.sh
make validate
make smoke
prometheus/run_rule_tests.sh
make package
```

Expected lightweight checks:

- `kaspa_watchtower_lightweight_mode` is `1`.
- `kaspa_watchtower_indexer_enabled` is `0`.
- `kaspa_watchtower_indexer_watch_enabled` is `0`.
- Daily report shows `Prometheus alerts: none`.
- Daily report shows Docker/indexer `containers=0`, `volumes=0`, and `images=0`.
- `scripts/ops_snapshot.sh` shows active alerts `0` and zero indexer Docker
  recreation counts.

## Deferred

- Companion indexer reactivation.
- Explorer API validation.
- Explorer/admin UI.
- Indexer-backed watchlist expansion.
