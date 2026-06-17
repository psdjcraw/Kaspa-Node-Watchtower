# Docker Image

Kaspa Node Watchtower can be built as a small Python runtime image for Docker
Hub and compose-based monitoring hosts. The image does not include `kaspad`, an
indexer, PostgreSQL, Prometheus, or Grafana. The bundled compose file includes
Caddy for serving the generated status page.

## Image Name

Default image targets:

```bash
psdjc/kaspa-node-watchtower:latest
psdjc/kaspa-node-watchtower:0.7.0
```

Override the repository or tag when building:

```bash
make docker-build DOCKER_IMAGE=yourname/kaspa-node-watchtower DOCKER_TAG=test
```

## Build And Smoke Test

```bash
make docker-build
make docker-smoke
```

`make docker-smoke` builds the image and runs `watchtower.py --version` inside
the container.

## Run One Command

Prepare a Docker config:

```bash
cp config.docker.example.json config.docker.json
```

Edit these fields:

- `node_name`
- `log_path`
- `data_dir`
- `rpc_endpoint`
- `grpc_endpoint`
- `thresholds`

For Docker Desktop on macOS, `host.docker.internal:16110` usually reaches a
host-local `kaspad` RPC/gRPC endpoint. On Linux, the bundled compose file adds a
`host.docker.internal` host-gateway entry.

Run a summary:

```bash
docker run --rm \
  -v "$PWD/config.docker.json:/config/config.json:ro" \
  -v "$PWD/state:/state" \
  -v "/path/to/kaspa/logs:/node/logs:ro" \
  -v "/path/to/kaspa/datadir:/node/datadir:ro" \
  psdjc/kaspa-node-watchtower:latest \
  -c /config/config.json --summary
```

## Compose

The bundled `docker-compose.yml` requires host paths through environment
variables:

```bash
export KASPAD_LOG_DIR=/path/to/kaspa/logs
export KASPAD_DATA_DIR=/path/to/kaspa/datadir
docker compose up --build
```

By default compose runs Watchtower every 5 minutes and serves the generated
status page through Caddy:

```bash
open http://127.0.0.1:8080/
```

Change the web port or watch interval if needed:

```bash
WATCHTOWER_WEB_PORT=18080 WATCHTOWER_INTERVAL_SECONDS=60 docker compose up --build
```

The Caddy service serves files from the shared `/state` volume. `/` rewrites to
`/status.html`, and `/watchtower.prom` exposes the latest textfile metrics when
Watchtower has generated them. `/game/` serves the bundled browser game.

## Process And Recovery Notes

Containers are best for RPC/gRPC, log, disk, dashboard, and Prometheus textfile
checks. Host process visibility is limited unless the host shares a process
namespace with the container, and launchd recovery does not fit a containerized
runtime.

For Linux hosts that need process checks, run with host PID visibility:

```bash
docker run --rm --pid=host ...
```

For macOS Docker Desktop, prefer RPC/gRPC and mounted log/data checks. Keep
`recovery.restart_command` empty in container configs unless you deliberately
mount a host control mechanism.

## Push To Docker Hub

Login once:

```bash
docker login
```

Build and push:

```bash
make docker-build
make docker-push
```

Push a version tag:

```bash
make docker-build DOCKER_TAG=0.7.0
make docker-push DOCKER_TAG=0.7.0
```

## GitHub Actions Publish

The bundled `.github/workflows/docker-publish.yml` builds and pushes multi-arch
`linux/amd64` and `linux/arm64` images on tag pushes and manual dispatch.

Required repository secrets:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

Release flow:

```bash
git tag v0.7.0
git push origin v0.7.0
```
