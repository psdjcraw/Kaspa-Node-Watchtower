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

## Hold Criteria

Do not cut v0.8.4 while all of these remain true:

- `scripts/ops_snapshot.sh` reports health 100, failed checks none, Prometheus
  alerts `0`, and lightweight/indexer-hold verdict OK.
- Docker indexer containers, volumes, and `simply-kaspa-indexer` images remain
  absent.
- Daily report, weekly report, status HTML, and Grafana continue to show the
  same release/lightweight posture.
- GitHub Actions smoke and CodeQL stay green on `main`.
- `make release-install-check` continues to verify the published v0.8.3 release
  asset and Homebrew formula.

Cut v0.8.4 only if one of these happens:

- alert, status, report, or Grafana polish fixes a real operator issue;
- release install checks fail;
- active Grafana provisioning drifts from the repository dashboard;
- a real post-v0.8.3 drift issue appears;
- the operator explicitly asks for a patch release.

## Stable Operating Mode

Use this order for routine checks:

1. `scripts/ops_snapshot.sh`
2. daily report
3. Grafana dashboard
4. Prometheus alerts
5. `state/status.html`

Noise budget:

- Immediate: node-health failures, exporter/Prometheus failures, disk pressure,
  release/install check failures, and unexpected indexer recreation.
- Observe: market-risk warnings and multi-node history imperfections.
- Silent: expected disabled-indexer posture while lightweight mode is enabled.

Stop condition: no more roadmap phases are needed until a real incident,
observed drift, explicit v0.8.4 release command, or a new operator-requested
scope appears.

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
