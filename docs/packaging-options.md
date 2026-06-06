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

## Candidate: Container Image

Use a container image when:

- the watchtower should run beside containerized Prometheus/Grafana
- host paths can be mounted read-only
- deployment should be uniform across Linux hosts

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
packaging-friendly structure before choosing a distribution channel.

Practical next steps:

- keep `make bootstrap`, `make validate`, `make smoke`, and `make ensure-exporter`
  as the stable install flow
- keep generated protobuf drift checks in CI
- add packaging only after config/state path conventions settle
- prefer Homebrew for macOS operator convenience if packaging is needed first
- prefer a container image only for Linux or compose-native deployments
