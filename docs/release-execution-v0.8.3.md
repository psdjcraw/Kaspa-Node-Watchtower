# v0.8.3 Release Execution Plan

This plan prepares the v0.8.3 release without publishing a tag or GitHub
Release. Tagging and publishing require a separate operator command.

## Phase 18 - Release Execution Prep

Before tagging:

- Confirm the release candidate commit is the intended `main` HEAD.
- Run the verification checklist in `docs/release-notes-v0.8.3.md`.
- Generate the final tracked-source package with `make package`.
- Record the generated package path and SHA-256 checksum.
- Confirm GitHub Actions smoke and CodeQL are green for the release candidate
  commit.
- Prepare the GitHub Release body from `docs/release-notes-v0.8.3.md`.
- Keep the Homebrew formula unchanged until the GitHub Release asset exists.

Generate the package after the final release-prep commit so the archive suffix
matches the commit being considered for release. Record the output in the
operator notes:

```text
release_package=dist/kaspa-node-watchtower-0.8.3-<commit>.tar.gz
release_checksum=dist/kaspa-node-watchtower-0.8.3-<commit>.tar.gz.sha256
```

## Phase 19 - 24 Hour Observation

Observe the lightweight host before release execution:

- Next daily report shows `Prometheus alerts: none`.
- `kaspa_watchtower_lightweight_mode` remains `1`.
- `kaspa_watchtower_indexer_enabled` remains `0`.
- `kaspa_watchtower_indexer_watch_enabled` remains `0`.
- Docker/indexer report remains `containers=0`, `volumes=0`, `images=0`.
- Disk free remains near the documented baseline and does not trend downward
  due to indexer recreation.
- Market risk warnings are readable and not confused with node incidents.

## Phase 20 - Release Decision

Only after Phase 19 is clean, decide whether to release:

```bash
git tag v0.8.3
git push origin v0.8.3
```

Then upload the package and checksum as GitHub Release assets. After the release
asset exists, update `packaging/homebrew/kaspa-node-watchtower.rb` with the
v0.8.3 URL and checksum in a follow-up commit.

## Phase 21 - Post-Release Watch

For 24 hours after release:

- Confirm daily report remains healthy.
- Confirm alert bridge stays quiet unless a real incident occurs.
- Confirm Grafana and status HTML still show lightweight mode.
- Confirm Discord `market` and `market-risk` output remains concise.
- Confirm Docker indexer resources are not recreated.

## Phase 22 - Backlog Reset

Keep the active backlog Watchtower-only:

- Report readability.
- Grafana lightweight panel.
- Market-risk noise tuning.
- Status UI polish.
- Weekly report compression.

Keep indexer, explorer API, and admin UI work in long-term backlog until the
indexer hold is explicitly lifted.
