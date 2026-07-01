# Kaspa Node Watchtower v0.8.4 Draft Notes

v0.8.4 is not released. This draft records post-v0.8.3 polish that is already
on `main` and can be packaged later if the operator decides the improvements
are worth a patch release.

## Candidate Scope

- Post-release lightweight drift checks after v0.8.3.
- Status page release posture cards for `Release` and `Indexer Hold`.
- Grafana `Lightweight Release Posture` panel and active provisioning sync.
- Alert `signal_type` routing labels for node health, node lifecycle, market
  risk, indexer lifecycle, watch, wallet, and whale signals.
- Weekly report market-section compression.
- `make release-install-check` for release tarball, checksum, extracted version,
  and Homebrew formula verification.

## Current Decision

Hold the v0.8.4 release for now. Keep the changes on `main` and continue
monitoring unless a post-v0.8.3 drift issue appears or the operator explicitly
asks to cut a patch release.

## Dry-Run Package

Generated from the current tracked source without a version bump:

```text
release_package=dist/kaspa-node-watchtower-v0.8.4-dry-run.tar.gz
release_checksum=dist/kaspa-node-watchtower-v0.8.4-dry-run.tar.gz.sha256
sha256=c50156281e643678ee21351ac2d4669b3f9ebd5554940cbd1559008e2943d375
```

## Verification

- `scripts/ops_snapshot.sh`: health 100, failed checks none, Prometheus alerts
  `0`, lightweight/indexer-hold verdict OK.
- `state/status.html`: includes Release, v0.8.3, Indexer Hold, Lightweight mode,
  SpaceX/private valuation content, and status timestamp metadata.
- Repository and active Grafana provisioning dashboards include `Lightweight
  Release Posture`.
- Daily report remains current-state focused.
- Weekly report market output is compressed to source, spot, futures,
  market-risk, and operator-action lines.

Before any real v0.8.4 tag, rerun:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile watchtower.py tests/test_watchtower.py
bash -n run_daily_report.sh run_weekly_report.sh scripts/ops_snapshot.sh scripts/check_release_install.sh
python3 -m json.tool grafana/kaspa-watchtower.json >/dev/null
make release-install-check
scripts/ops_snapshot.sh
./run_daily_report.sh
./run_weekly_report.sh
make validate
prometheus/run_rule_tests.sh
make smoke
```
