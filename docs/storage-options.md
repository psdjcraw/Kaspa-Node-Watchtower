# Long-Lived Storage Options

Kaspa Node Watchtower currently stores long-window history in local SQLite. That
is enough for a single operator host, but it is not a durable multi-host storage
layer. This note captures candidate options for v0.4+ planning.

## Current Default: Local SQLite

Use local SQLite when:

- the watchtower runs on one host
- history is mainly for local operator reports
- the operator wants no network dependency
- backups can capture `state/watchtower-history.sqlite`

Keep using:

```bash
make export-history
make history-report
make weekly-report
```

Risks:

- host-local only
- no built-in replication
- state can be lost if the node host disk fails without backups

## Candidate: Prometheus Remote Storage

Use Prometheus remote write or a managed Prometheus-compatible backend when:

- metrics already drive dashboards and alerts
- long retention is needed for time-series trends
- operators want Grafana queries across months

Good fit:

- health status
- severity
- peer counts
- sync rates
- relay freshness
- recovery counters
- benchmark stability gauges

Weak fit:

- full JSON reports
- diagnostics bundles
- rich upgrade checkpoint metadata

## Candidate: Object Storage Archives

Use S3-compatible object storage, Backblaze B2, local NAS, or another archive
target when:

- diagnostics bundles need durable retention
- reports should be preserved after incidents
- local node disk should not be the only copy

Good fit:

- diagnostics archives
- weekly reports
- status HTML snapshots
- exported SQLite snapshots

Weak fit:

- real-time alerting
- ad hoc metric queries

## Candidate: External SQL

Use PostgreSQL or another external SQL database when:

- multiple watchtower hosts need a shared history database
- richer incident review queries are needed
- operational overhead is acceptable

Good fit:

- benchmark snapshots
- recovery attempts
- upgrade checkpoints
- report metadata

Weak fit:

- minimal single-node deployments
- operators who want zero extra services

## Recommended Path

For the current deployment, keep local SQLite as the default and add optional
archive export before adding a new database dependency.

Practical next steps:

- keep `state/watchtower-history.sqlite` in host backups
- archive `state/diagnostics/*.tar.gz` after incidents
- consider a weekly archive job for `make weekly-report` output and SQLite
  snapshots
- use Prometheus long retention or remote write if month-scale Grafana history
  becomes important
