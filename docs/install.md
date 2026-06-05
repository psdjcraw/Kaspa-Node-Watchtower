# Installation

Kaspa Node Watchtower is local-first. Install it on the same host that can read
the local `kaspad` process, logs, data directory, and RPC/gRPC endpoint.

## Requirements

- Python 3.12 or newer
- `kaspad` running locally or reachable over local/private networking
- Access to the node log path and data directory
- Optional: Docker Prometheus/Grafana stack for dashboards and alert rules

## Bootstrap Python and Protobuf

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
make summary
make smoke
```

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

## Optional launchd Exporter

On the current macOS host, the LaunchAgent plist is:

```text
launchd/com.openclaw.kaspa-watchtower-prometheus.plist
```

Install or repair it with:

```bash
make ensure-exporter
```

Manual bootstrap uses the exact local workspace path:

```bash
launchctl bootstrap gui/$(id -u) /Users/psdjc/.openclaw/workspace/Kaspa-Node-Watchtowe/launchd/com.openclaw.kaspa-watchtower-prometheus.plist
launchctl kickstart -k gui/$(id -u)/com.openclaw.kaspa-watchtower-prometheus
```

The local directory name may still be `Kaspa-Node-Watchtowe`; use the actual
local path for launchd and cron commands.
