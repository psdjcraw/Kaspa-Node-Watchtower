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

Human-readable status:

```bash
.venv/bin/python watchtower.py -c config.json
```

JSON status for automation:

```bash
.venv/bin/python watchtower.py -c config.json --json
```

Alert mode for cron:

```bash
./run_watchtower.sh
```

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
