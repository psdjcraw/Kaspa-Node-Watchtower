# v0.4.0 Release Notes

Kaspa Node Watchtower v0.4.0 focuses on operator automation: safer recovery
decisions, richer incident summaries, weekly review output, portable history
archives, and a source tarball packaging path.

## Highlights

- Recovery dry-runs now print a decision block before any action.
- Diagnostics bundles start with a sanitized incident summary for issue reports.
- Weekly operator reports summarize diagnostics, 7-day and 30-day history,
  benchmark trends, recovery attempts, and upgrade checkpoints.
- Portable history archives preserve SQLite history, source JSONL files, summary
  JSON, and a manifest for off-host backup.
- Release packaging builds a tracked-source tarball with a package manifest and
  SHA-256 checksum.
- Failure simulations now explicitly cover stalled relay block warning output in
  addition to stale logs, missing gRPC metrics, disk pressure, and exporter
  health failures.

## Operator Commands

```bash
make weekly-report
make weekly-archive
make history-archive
make package
```

## Verification Checklist

Before tagging v0.4.0, verify:

```bash
make version
python3 -m unittest discover -s tests
make smoke
make integrations
make package
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.

## Known Limitations

- SQLite remains the default history store.
- Portable archives are local directories; copying them to NAS or object storage
  is still an operator policy decision.
- Packaging ships tracked source files, docs, rules, dashboards, and scripts. It
  does not install services or generate host-specific `config.json`.
