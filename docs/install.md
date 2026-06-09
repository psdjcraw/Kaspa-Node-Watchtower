# Installation

Kaspa Node Watchtower is local-first. Install it on the same host that can read
the local `kaspad` process, logs, data directory, and RPC/gRPC endpoint.

For the current v0.8 fresh-host handoff path, use
`docs/release-install-guide-v0.8.md` after this base install guide.

## Requirements

- Python 3.12 or newer
- `kaspad` running locally or reachable over local/private networking
- Access to the node log path and data directory
- Optional: Docker Prometheus/Grafana stack for dashboards and alert rules

## Bootstrap Python and Protobuf

For a guided, check-only first pass, run:

```bash
make onboard
```

This prints workspace, Python, config, state, launchd, and next-step hints
without installing services or overwriting config. Optional modes:

```bash
scripts/onboard_local.sh --write-config
scripts/onboard_local.sh --bootstrap
scripts/onboard_local.sh --smoke
```

Run:

```bash
make bootstrap
```

This runs:

```bash
scripts/bootstrap_env.sh
```

The bootstrap script creates `.venv` if needed, installs `requirements.txt`,
and regenerates the gRPC protobuf modules under `generated_proto/`.

Use a custom Python or virtualenv path if needed:

```bash
PYTHON_BIN=python3.12 VENV_DIR=.venv scripts/bootstrap_env.sh
```

## Configure Local Node Paths

Copy the example config:

```bash
cp config.example.json config.json
```

Edit:

- `node_name`
- `process_match`
- `rpc_endpoint`
- `grpc_endpoint`
- `log_path`
- `data_dir`
- `recovery.restart_command`

`config.json` is ignored by git. Do not commit machine-specific paths, private
hostnames, webhook URLs, tokens, logs, or diagnostics archives.

## Validate

Run:

```bash
make validate
make proto-check
make summary
make smoke
```

`make validate` prints failed setting names with expected value hints when a
path, endpoint, threshold, boolean flag, or retention setting needs correction.
For v0.8 host handoff, validation also checks `sqlite_history_path`,
node-name slug/network hints for multi-node history, distinct state/history
paths, bundled Prometheus rule and Grafana dashboard files, and optional
`MULTI_NODE_*` threshold environment overrides when they are set.
`make proto-check` verifies that checked-in generated protobuf files match
`proto/rpc.proto` and `proto/messages.proto`.

For external endpoints and dashboards:

```bash
make integrations
```

## Optional Prometheus Exporter

Write a textfile once:

```bash
make prometheus
```

Run the local HTTP exporter:

```bash
./run_prometheus_exporter.sh
```

See `docs/integrations.md` for Prometheus/Grafana scrape, rule, and dashboard
setup.

## Optional launchd Services

Render and dry-run the managed LaunchAgents first:

```bash
scripts/manage_launchd.sh --service exporter print
scripts/manage_launchd.sh install
```

Install or repair the full local service set on macOS:

```bash
make launchd-install
make launchd-status
```

Managed services:

- `exporter`: Prometheus HTTP exporter, kept alive
- `status`: alert-mode health check every 5 minutes
- `benchmark`: benchmark snapshot every 30 minutes
- `daily`: daily operator report at 09:10
- `weekly`: weekly operator report on Monday at 09:30
- `alerts`: Prometheus alert bridge every 5 minutes
- `smoke`: daily smoke test at 03:20

Reload or remove the full set:

```bash
make launchd-restart
make launchd-uninstall
```

The manager renders plists with the current checkout path before installing
them to `~/Library/LaunchAgents`. The local directory name may still be
`Kaspa-Node-Watchtowe`; use the actual local path for launchd and cron
commands. `make ensure-exporter` remains available for repairing only the
Prometheus exporter LaunchAgent.
