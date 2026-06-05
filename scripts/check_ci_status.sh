#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

GITHUB_REPOSITORY="${KASPA_WATCHTOWER_GITHUB_REPOSITORY:-psdjcraw/Kaspa-Node-Watchtowe}"
GITHUB_WORKFLOW="${KASPA_WATCHTOWER_GITHUB_WORKFLOW:-smoke.yml}"
GITHUB_BRANCH="${KASPA_WATCHTOWER_GITHUB_BRANCH:-main}"
API_URL="https://api.github.com/repos/$GITHUB_REPOSITORY/actions/workflows/$GITHUB_WORKFLOW/runs"

curl_args=(-fsS -G "$API_URL" --data-urlencode "branch=$GITHUB_BRANCH" --data-urlencode "per_page=1")
if [ -n "${GITHUB_TOKEN:-}" ]; then
  curl_args=(-H "Authorization: Bearer $GITHUB_TOKEN" "${curl_args[@]}")
fi

curl "${curl_args[@]}" |
  python3 -c 'import json, sys

data = json.load(sys.stdin)
runs = data.get("workflow_runs", [])
if not runs:
    print("FAIL GitHub Actions: no workflow runs found", file=sys.stderr)
    raise SystemExit(1)

run = runs[0]
name = run.get("name", "workflow")
status = run.get("status", "unknown")
conclusion = run.get("conclusion")
sha = (run.get("head_sha") or "")[:7] or "unknown"
url = run.get("html_url", "")
created = run.get("created_at", "unknown")

if status == "completed" and conclusion == "success":
    print(f"OK GitHub Actions {name}: success {sha} {created} {url}")
    raise SystemExit(0)

print(
    f"FAIL GitHub Actions {name}: status={status} conclusion={conclusion} "
    f"sha={sha} created={created} {url}",
    file=sys.stderr,
)
raise SystemExit(1)'
