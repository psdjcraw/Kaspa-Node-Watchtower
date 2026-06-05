# Operations

## Current Target

- Node: `kaspa-tn10-local`
- Host: `hang-studio-m4max`
- Network: Kaspa testnet 10
- RPC TCP check: `127.0.0.1:16210`
- Process match: `kaspad`
- Log: `/Users/psdjc/kaspa/rusty-kaspa-tn10-data/kaspa-testnet-10/logs/rusty-kaspa.log`
- Data dir: `/Users/psdjc/kaspa/rusty-kaspa-tn10-data/kaspa-testnet-10/datadir`

## Alert Criteria

The local watchtower reports an alert when any of these fail:

- `kaspad` process is not running.
- Data directory is missing.
- RPC TCP connection to `127.0.0.1:16210` fails.
- Disk free space drops below `20 GiB` or below `5%`.
- The latest `kaspad` log timestamp is older than `15 minutes`.
- The configured log file is missing.

## Commands

Human-readable status:

```bash
python3 watchtower.py -c config.json
```

JSON status for automation:

```bash
python3 watchtower.py -c config.json --json
```

Alert mode for cron:

```bash
./run_watchtower.sh
```

`--alert` writes state to `state/watchtower-state.json`. It emits output when
status changes, and keeps emitting while the status remains `alert`.

## Discord Cron Plan

OpenClaw cron job `d370358a-e1f3-4456-9818-68537c558f88`
(`kaspa-node-watchtower-alerts`) runs every 10 minutes in an isolated session.
The cron prompt runs:

```bash
cd /Users/psdjc/.openclaw/workspace/Kaspa-Node-Watchtowe && ./run_watchtower.sh
```

If the command prints nothing, stay quiet. If it prints text, post the concise
output to the Discord thread for the Kaspa watchtower.
