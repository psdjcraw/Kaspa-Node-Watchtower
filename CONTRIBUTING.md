# Contributing

Thanks for helping improve Kaspa Node Watchtower. This project is focused on
small, reliable operator tooling for local `kaspad` nodes, so contributions
should keep local operation, clear diagnostics, and safe defaults in mind.

## Getting Started

Create a local virtual environment and install the Python dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Generate the protobuf files used by the gRPC metrics integration:

```bash
.venv/bin/python -m grpc_tools.protoc -I proto --python_out=generated_proto --grpc_python_out=generated_proto proto/rpc.proto proto/messages.proto
```

Copy the example configuration for local testing:

```bash
cp config.example.json config.json
```

`config.json` is ignored by git. Do not commit local node paths, private host
details, credentials, diagnostics archives, or generated state files.

## Development Workflow

Before opening a pull request, run the relevant checks:

```bash
python3 -m unittest discover -s tests
scripts/smoke_test.sh
prometheus/run_rule_tests.sh
```

The full local smoke check is also available through `make`:

```bash
make smoke
```

For changes that touch operational behavior, also test the affected operator
command when possible:

```bash
make summary
make validate
make daily-report
make integrations
```

Some commands expect a real local `kaspad` setup and a valid `config.json`.
If you cannot run those checks, mention that in the pull request.

## Pull Request Guidelines

- Keep changes focused and easy to review.
- Add or update tests for parser, alerting, recovery, metrics, or reporting
  behavior.
- Update `README.md` or `docs/` when changing operator commands, alert
  criteria, dashboards, runbooks, or configuration.
- Prefer explicit, operator-readable error messages over silent fallback.
- Preserve local-first behavior. The watchtower should not require hosted APIs
  for core node health reporting.
- Avoid committing generated diagnostics, local benchmark history, SQLite
  exports, logs, or machine-specific configuration.

## Reporting Issues

When filing an issue, include:

- The command you ran.
- The relevant sanitized output or traceback.
- Your `kaspad` network, version, and whether the node is syncing or already in
  relay mode.
- Whether `make smoke` or `python3 -m unittest discover -s tests` passes.

Do not include private hostnames, credentials, wallet data, or unsanitized logs.

## License

By contributing, you agree that your contributions are licensed under the
Apache License, Version 2.0.
