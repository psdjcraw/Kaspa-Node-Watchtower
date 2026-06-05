# Operations

## Current Target

- Node: `kaspa-tn10-local`
- Host: `hang-studio-m4max`
- Network: Kaspa testnet 10
- RPC TCP check: `127.0.0.1:16210`
- gRPC metrics: `127.0.0.1:16210`
- Process match: `kaspad`
- Log: `/Users/psdjc/kaspa/rusty-kaspa-tn10-data/kaspa-testnet-10/logs/rusty-kaspa.log`
- Data dir: `/Users/psdjc/kaspa/rusty-kaspa-tn10-data/kaspa-testnet-10/datadir`

## Alert Criteria

The local watchtower reports an alert when any of these fail:

- `kaspad` process is not running.
- Data directory is missing.
- RPC TCP connection to `127.0.0.1:16210` fails.
- gRPC metrics cannot be read.
- Node reports `isSynced=false`.
- Connected peer count is below `1`.
- Disk free space drops below `20 GiB` or below `5%`.
- The latest `kaspad` log timestamp is older than `15 minutes`.
- No relay-accepted blocks appear in the latest `10 minutes`.
- The configured log file is missing.

Severity mapping:

- `critical`: process, data directory, RPC, gRPC, sync, peer count, or log file failure.
- `warn`: disk, log freshness, or relay progress failure.
- `ok`: all checks pass.

Alert repeat suppression:

- Status or severity transitions are announced immediately.
- Ongoing non-OK states repeat at most once every `60` minutes.
- Healthy repeated checks stay quiet.

Recovery:

- Current mode is `manual`.
- The watchtower can include the configured restart command in alert context,
  but it does not restart the healthy node automatically.
- Manual recovery command is available through `--recover`.
- A healthy node is never restarted unless `--force-recover` is explicitly used.

## Commands

Integration verification:

```bash
scripts/check_integrations.sh
```

GitHub Actions smoke status:

```bash
scripts/check_ci_status.sh
```

Operational snapshot:

```bash
scripts/ops_snapshot.sh
```

Full local smoke test:

```bash
scripts/smoke_test.sh
```

Cron-friendly smoke test:

```bash
./run_smoke_test.sh
```

Daily operator report:

```bash
./run_daily_report.sh
```

Human-readable status:

```bash
.venv/bin/python watchtower.py -c config.json
```

JSON status for automation:

```bash
.venv/bin/python watchtower.py -c config.json --json
```

Concise operator summary:

```bash
.venv/bin/python watchtower.py -c config.json --summary
```

Config validation:

```bash
.venv/bin/python watchtower.py -c config.json --validate-config
```

Alert mode for cron:

```bash
./run_watchtower.sh
```

Benchmark snapshot:

```bash
.venv/bin/python watchtower.py -c config.json --benchmark-snapshot
```

Cron-friendly benchmark snapshot:

```bash
./run_benchmark_snapshot.sh
```

Benchmark report:

```bash
.venv/bin/python watchtower.py -c config.json --benchmark-report
```

Apply retention limits:

```bash
.venv/bin/python watchtower.py -c config.json --prune-state
```

Use benchmark snapshots before and after `rusty-kaspa` upgrades or configuration
changes. The report compares DAA score, block count, relay progress, peer state,
severity counts, and disk free space across the saved window.

Retention defaults keep the latest `100` alert/status history entries and latest
`1000` benchmark snapshots. Benchmark snapshot runs prune old snapshots
automatically after appending a new one.

Prometheus textfile metrics:

```bash
.venv/bin/python watchtower.py -c config.json --prometheus
```

Alert-mode runs also refresh `state/watchtower.prom`, so the cron health check
keeps the textfile fresh for local scraping or textfile collectors.

Prometheus HTTP exporter:

```bash
./run_prometheus_exporter.sh
curl -fsS http://127.0.0.1:9660/metrics
```

LaunchAgent install/restart:

```bash
launchctl bootstrap gui/$(id -u) /Users/psdjc/.openclaw/workspace/Kaspa-Node-Watchtowe/launchd/com.openclaw.kaspa-watchtower-prometheus.plist
launchctl kickstart -k gui/$(id -u)/com.openclaw.kaspa-watchtower-prometheus
```

Prometheus scrape target from the existing Docker stack:

```yaml
  - job_name: kaspa-watchtower
    scrape_interval: 15s
    static_configs:
      - targets:
          - host.docker.internal:9660
```

The local `asus-traffic-monitor` Prometheus config has this scrape job applied.
Prometheus target health can be checked at:

```text
http://127.0.0.1:9090/targets?search=kaspa-watchtower
```

Grafana dashboard:

```text
grafana/kaspa-watchtower.json
```

Provisioned local Grafana URL:

```text
http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower
```

Prometheus alert rules:

```text
prometheus/kaspa-watchtower-rules.yml
```

Prometheus alert rule tests:

```bash
prometheus/run_rule_tests.sh
```

GitHub Actions:

```text
https://github.com/psdjcraw/Kaspa-Node-Watchtowe/actions/workflows/smoke.yml
```

The local CI checker reads the latest `main` run through the GitHub Actions API.
Set `GITHUB_TOKEN` if the API becomes rate-limited or the repository visibility
changes.

The local `asus-traffic-monitor` Prometheus stack has the rule file copied to
`prometheus-rules/kaspa-watchtower-rules.yml` and mounted at
`/etc/prometheus/rules/kaspa-watchtower-rules.yml`. Current rules:

- `KaspaWatchtowerExporterDown`
- `KaspaWatchtowerCritical`
- `KaspaWatchtowerWarning`
- `KaspaNodePeerCountLow`
- `KaspaRelayProgressStalled`
- `KaspaWatchtowerMetricsStale`

Manual recovery dry-run:

```bash
.venv/bin/python watchtower.py -c config.json --recover --dry-run
```

Manual recovery when an alert requires it:

```bash
.venv/bin/python watchtower.py -c config.json --recover
```

`--alert` writes state to `state/watchtower-state.json`. It emits output when
status changes, and keeps emitting while the status remains `alert`.

The cron wrapper prefers `.venv/bin/python` so the gRPC dependencies can stay
local to this repository.

HTML status page:

```bash
open state/status.html
```

The generated status page includes the latest health checks, recent check
history, and a benchmark trend section from the latest saved snapshots.

Daily report:

```bash
./run_daily_report.sh
```

The daily report prints the current node summary, benchmark trend, integration
status, GitHub Actions status, and dashboard locations. Unlike alert and smoke
wrappers, it intentionally emits output while healthy.

Prometheus textfile metrics:

```text
state/watchtower.prom
```

Prometheus HTTP endpoint:

```text
http://127.0.0.1:9660/metrics
```

Canvas-hosted status page file:

```text
/Users/psdjc/.openclaw/canvas/kaspa-watchtower/status.html
```

The gateway route is auth-protected. If a node is paired later, present it with:

```bash
openclaw nodes canvas present --node <node> --target /kaspa-watchtower/status.html
```

Simulation test without touching the live node:

```bash
jq '.state_path="state/sim-watchtower-state.json"
  | .status_page_path="state/sim-status.html"
  | .thresholds.min_peer_count=999' config.json > /tmp/kaspa-watchtower-sim.json
.venv/bin/python watchtower.py -c /tmp/kaspa-watchtower-sim.json --alert
.venv/bin/python watchtower.py -c /tmp/kaspa-watchtower-sim.json --alert
```

The first simulated run should print a `critical` alert. The second run should
stay quiet because repeat suppression is active.

## Git Push

This repository uses the registered GitHub deploy key at:

```bash
/Users/psdjc/.ssh/openclaw_git_20260605_ed25519
```

The local repo config should keep:

```bash
git config core.sshCommand "ssh -i /Users/psdjc/.ssh/openclaw_git_20260605_ed25519 -o IdentitiesOnly=yes"
```

## Discord Cron Plan

OpenClaw cron job `d370358a-e1f3-4456-9818-68537c558f88`
(`kaspa-node-watchtower-alerts`) runs every 10 minutes in an isolated session.
The cron prompt runs:

```bash
cd /Users/psdjc/.openclaw/workspace/Kaspa-Node-Watchtowe && ./run_watchtower.sh
```

If the command prints nothing, stay quiet. If it prints text, post the concise
output to the Discord thread for the Kaspa watchtower.

OpenClaw cron job `aef87796-2552-4cf6-b8ff-897b9ce3ca99`
(`kaspa-watchtower-benchmark-snapshots`) runs every 30 minutes in an isolated
session. It is separate from alerting and executes:

```bash
cd /Users/psdjc/.openclaw/workspace/Kaspa-Node-Watchtowe && ./run_benchmark_snapshot.sh
```

The benchmark wrapper writes the latest snapshot output to
`state/last-benchmark-snapshot.txt`, appends structured data to
`state/benchmarks.jsonl`, and prints only when the snapshot command fails.

OpenClaw cron job `a7e56678-da5c-43dd-8d04-0f3e6e21f1cd`
(`kaspa-watchtower-daily-smoke-test`) runs the full smoke test daily at 03:20
KST in an isolated session. It executes:

```bash
cd /Users/psdjc/.openclaw/workspace/Kaspa-Node-Watchtowe && ./run_smoke_test.sh
```

The smoke test wrapper writes the latest successful output to
`state/last-smoke-test.txt` and prints only when the smoke test fails.
