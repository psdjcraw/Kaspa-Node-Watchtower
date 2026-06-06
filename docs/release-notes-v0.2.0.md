# Kaspa Node Watchtower v0.2.0 Release Notes

Release date: 2026-06-06

v0.2.0 focuses on easier installation, clearer operations, richer local
dashboards, and safer release-readiness checks for self-hosted `kaspad`
operators.

## Highlights

- Visual `status.html` dashboard with health bars, compact cards, and history
  sparklines.
- Daily operator report verdict with SQLite history summary, integration status,
  and GitHub Actions status.
- `make ensure-exporter` for installing, restarting, and verifying the local
  Prometheus exporter LaunchAgent.
- SQLite history summary via `make history-report`.
- Grafana panels for relay freshness, bootstrap progress rates, recovery action
  mix, benchmark stability, and benchmark floors.
- Config validation with failed-setting summaries and expected value hints.
- Failure simulations for peer, relay, RPC, missing gRPC metrics, disk pressure,
  stale logs, repeat suppression, recovered transitions, and recovery dry-runs.
- Sanitized sample status reports for healthy, bootstrap, critical RPC/gRPC
  failure, and disk pressure states.

## Upgrade

From an existing checkout:

```bash
git pull
make bootstrap
make validate
make smoke
make integrations
make ensure-exporter
```

Refresh local Grafana provisioning if you use the bundled dashboard:

```bash
cp grafana/kaspa-watchtower.json /Users/psdjc/.openclaw/workspace/asus-traffic-monitor/grafana/dashboards/kaspa-watchtower.json
cd /Users/psdjc/.openclaw/workspace/asus-traffic-monitor
docker compose restart grafana
```

## Verification

The v0.2.0 release candidate was verified with:

```bash
python3 -m unittest discover -s tests
make smoke
make integrations
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.

## Known Limitations

- The watchtower is local-first and does not replace hosted explorers for broad
  network comparison.
- `config.json`, local state files, diagnostics archives, logs, and node paths
  are host-specific and should not be committed.
- gRPC metrics depend on the bundled protobuf definitions matching the current
  `rusty-kaspa` API.
- LaunchAgent repair is macOS-specific.
- Long-window history storage is local SQLite only in this release.
- Recovery execution is manual-command based and should be reviewed before any
  non-dry-run restart.
