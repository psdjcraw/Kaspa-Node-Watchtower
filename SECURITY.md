# Security Policy

Kaspa Node Watchtower is local-first operator tooling. It reads local process
state, local logs, gRPC/RPC endpoints, and generated monitoring state. Treat
diagnostics and configuration as sensitive unless they have been sanitized.

## Supported Versions

The `main` branch is the actively maintained version.

## Automated Checks

The repository runs smoke tests and CodeQL analysis on `main` and pull requests.
Passing automation is not a substitute for reviewing local configs, generated
diagnostics, or recovery commands before sharing them.

## Reporting Security Issues

Do not post secrets, private hostnames, wallet data, unsanitized logs, SSH
details, webhook URLs, or diagnostics archives in public issues.

If you find a security issue:

1. Reproduce it with the smallest local command possible.
2. Remove credentials, hostnames, wallet data, and private paths from examples.
3. Contact the maintainer privately when possible.
4. If a public issue is the only option, describe the impact without publishing
   exploit details or sensitive artifacts.

Useful sanitized context:

- Watchtower command and version or commit.
- Kaspa network and node state.
- Sanitized config keys involved.
- Whether `make smoke`, `make validate`, or `prometheus/run_rule_tests.sh`
  passes.

## Local Data Handling

Do not commit these files or their contents:

- `config.json`
- `state/`
- diagnostics archives
- local logs
- generated SQLite history
- webhook URLs or bot tokens
- SSH keys or launchd user-specific secrets

## Operational Safety

Recovery commands are manual by default. A healthy node is not restarted unless
`--force-recover` is explicitly used. When testing recovery behavior, prefer
`--recover --dry-run` or `scripts/simulate_failures.sh`.
