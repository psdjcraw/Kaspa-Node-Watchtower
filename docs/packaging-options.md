# Packaging Options

Kaspa Node Watchtower is currently a source checkout with a Python virtualenv,
shell wrappers, launchd plist, Prometheus rules, and Grafana dashboard JSON. That
is appropriate for the current macOS operator host, but v0.4+ can make
deployment easier.

## Current Default: Source Checkout

Use the source checkout when:

- the operator wants full local control
- launchd paths are host-specific
- shell scripts and docs are part of the workflow
- local edits and rapid iteration matter

Setup:

```bash
git clone <repo>
make bootstrap
cp config.example.json config.json
make validate
make smoke
make ensure-exporter
```

Pros:

- simplest for development
- transparent scripts and state files
- no packaging tooling required

Cons:

- host paths must be configured manually
- updates require `git pull`
- generated protobuf files and virtualenv must be maintained

## Current Portable Package: Release Tarball

Use the release tarball when:

- the operator wants a clean snapshot of tracked project files
- local config and state must stay out of the artifact
- a GitHub Release asset, NAS copy, or manual install bundle is enough

Build:

```bash
make package
scripts/package_release.sh --dist-dir dist
scripts/package_release.sh --dist-dir dist --label v0.4.0-rc1
```

Output:

- `dist/kaspa-node-watchtower-<version>-<revision>.tar.gz`
- matching `.sha256` checksum
- `PACKAGE-MANIFEST.json` inside the tarball

The tarball is generated from `git ls-files`, so local `config.json`, `state/`,
virtualenvs, diagnostics bundles, and other host-specific files are excluded.

## Candidate: Python Wheel

Use a Python wheel when:

- command-line entrypoints should be installed cleanly
- dependencies should be resolved by Python packaging tools
- generated protobuf files are stable enough to ship as package data

Pros:

- standard Python install flow
- easier command entrypoints
- cleaner versioning

Cons:

- shell wrappers, launchd plist, Grafana JSON, and Prometheus rules still need
  deployment handling
- local state paths remain host-specific

## Candidate: Homebrew Formula

Use a Homebrew formula when:

- macOS is the primary operator platform
- launchd and local service management remain important
- users expect `brew install` and `brew services`

Pros:

- strong macOS fit
- service lifecycle can be documented around Homebrew
- good for a small operator tool

Cons:

- formula maintenance overhead
- less useful for Linux nodes
- still needs config bootstrap

Current formula:

```text
packaging/homebrew/kaspa-node-watchtower.rb
```

The formula installs the v0.8.1 release archive, exposes
`kaspa-watchtower`, and prints post-install checks for `--version` and
`--validate-config`. A source checkout remains the recommended path for full
operator smoke, launchd service management, Prometheus/Grafana files, and
wrapper scripts.

Formula syntax check:

```bash
ruby -c packaging/homebrew/kaspa-node-watchtower.rb
```

## Docker Hub Image

Use the Docker image when:

- the watchtower should run beside containerized Prometheus/Grafana
- host paths can be mounted read-only
- deployment should be uniform across Linux hosts

Build and smoke test:

```bash
make docker-smoke
```

Push to Docker Hub:

```bash
docker login
make docker-build DOCKER_TAG=0.8.1
make docker-push DOCKER_TAG=0.8.1
```

Default image:

```text
psdjc/kaspa-node-watchtower
```

See `docs/docker.md` for compose, mounts, and host process caveats.
The bundled GitHub Actions workflow can publish multi-arch Docker Hub images
when `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` repository secrets are set.

Pros:

- repeatable runtime
- isolated dependencies
- easier for compose-based monitoring stacks

Cons:

- reading host process state and logs needs careful mounts
- launchd recovery commands do not fit inside a container
- macOS host process visibility is weaker from containers

## Recommended Path

For the current deployment, keep source checkout as the default and prepare a
portable release tarball before choosing a heavier distribution channel.

Practical next steps:

- keep `make bootstrap`, `make validate`, `make smoke`, and `make ensure-exporter`
  as the stable install flow
- keep generated protobuf drift checks in CI
- use `make package` for GitHub Release assets or manual operator bundles
- iterate on `packaging/homebrew/kaspa-node-watchtower.rb` for macOS operator
  convenience
- prefer a container image only for Linux or compose-native deployments
