# Lightweight Indexer Mode

This host currently runs Watchtower in a lightweight mode: the Python
Watchtower stays active, while the companion PostgreSQL-backed
`simply-kaspa-indexer` stack is intentionally disabled.

## Current Contract

- Keep `config.json` set to `"indexer": {"enabled": false}` and
  `"indexer_watch": {"enabled": false}` for the local mainnet deployment.
- Keep the `simply-kaspa-indexer` source checkout available for future work,
  but do not keep its Docker containers, images, build cache, or PostgreSQL
  volume running by default.
- Treat `indexer=disabled reason=config ok=True probes=skipped` as a healthy
  Watchtower state.
- Keep the operational SLO centered on `kaspad` process state, gRPC metrics,
  relay progress, peer count, log freshness, and disk free space.
- Treat indexer-backed explorer/admin work as long-term backlog. Do not schedule
  it in the active lightweight-operation plan.

## Long-Term Hold

The companion indexer is on long-term hold for this mainnet host. The hold is
intentional, not an incident:

- The PostgreSQL-backed chain index can grow quickly and compete with `kaspad`
  disk headroom.
- Retention, pruning, and backup rules need to be decided before the DB volume
  is recreated.
- Docker image/build-cache growth should stay at zero during lightweight
  operation.
- Watchtower already provides the active operator surface for node health,
  market risk, alerts, reports, Grafana, and status HTML.

Only revisit the hold after explicitly deciding:

- Minimum disk headroom for indexer reactivation.
- PostgreSQL volume retention and deletion policy.
- DB prune/cleanup strategy.
- Docker cleanup and rollback commands.
- The intended `simply-kaspa-indexer` upstream or fork commit.
- The exact API scope needed by Watchtower before explorer/admin UI work starts.

## Auto-Recreation Audit

The normal macOS LaunchAgents for this host run Watchtower status checks,
reports, Prometheus export, alert bridge, smoke tests, benchmark snapshots,
and `kaspad`. They do not run `make indexer-up` or the
`integrations/simply-kaspa-indexer/docker-compose.yml` stack.

There is no crontab entry for the indexer stack on this host.

The remaining start path is intentionally manual:

```bash
CONFIRM_INDEXER_UP=1 make indexer-up
```

The confirmation flag prevents accidental Docker volume and image recreation
from a casual `make indexer-up`.

## Re-Enable Checklist

Before re-enabling the indexer on this host:

- Confirm at least 150 GiB of spare disk capacity beyond the current `kaspad`
  datadir footprint.
- Decide the PostgreSQL volume retention policy before starting the stack.
- Confirm Docker image, build cache, and volume cleanup commands are available.
- Confirm `simply-kaspa-indexer` source is pinned to the intended fork or
  upstream commit.
- Start the stack with `CONFIRM_INDEXER_UP=1 make indexer-up`.
- Run `make indexer-smoke`.
- Set `indexer.enabled=true` and `indexer_watch.enabled=true` only after the
  health and metrics endpoints are responsive.
- Run `python3 watchtower.py -c config.json --summary` and confirm the indexer
  line reports a real healthy state.

## Rollback

To return to lightweight mode:

```bash
make indexer-down
docker volume rm simply-kaspa-indexer_kaspa-db-data
docker builder prune
```

Then restore `config.json` to disabled indexer settings and verify:

```bash
python3 watchtower.py -c config.json --summary
```

Expected healthy line:

```text
indexer=disabled reason=config ok=True probes=skipped
```
