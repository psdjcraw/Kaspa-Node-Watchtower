#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

EXPORTER_URL="${KASPA_WATCHTOWER_EXPORTER_URL:-http://127.0.0.1:9660}"
PROMETHEUS_URL="${KASPA_WATCHTOWER_PROMETHEUS_URL:-http://127.0.0.1:9090}"
GRAFANA_URL="${KASPA_WATCHTOWER_GRAFANA_URL:-http://127.0.0.1:3000}"
GRAFANA_DASHBOARD_PATH="${KASPA_WATCHTOWER_GRAFANA_DASHBOARD_PATH:-/d/kaspa-watchtower/kaspa-watchtower}"
RELEASE_TAG="${KASPA_WATCHTOWER_RELEASE_TAG:-v0.8.3}"
RELEASE_VERSION="${RELEASE_TAG#v}"
LAUNCHD_LABEL="com.openclaw.kaspa-watchtower-prometheus"

section() {
  printf '\n== %s ==\n' "$1"
}

prom_query() {
  local query="$1"
  curl -fsG "$PROMETHEUS_URL/api/v1/query" --data-urlencode "query=$query" |
    python3 -c 'import json, sys
data = json.load(sys.stdin)
result = data.get("data", {}).get("result", [])
if not result:
    print("missing")
else:
    print(result[0].get("value", ["", "missing"])[1])'
}

exporter_metric() {
  local metric="$1"
  local metrics
  metrics="$(curl -fsS "$EXPORTER_URL/metrics" 2>/dev/null || true)"
  printf '%s\n' "$metrics" |
    awk -v metric="$metric" '
      !found && $1 ~ ("^" metric "(\\{|$)") {print $2; found=1}
      END {if (!found) print "missing"}
    '
}

docker_count_matching() {
  local kind="$1"
  local pattern="$2"
  if ! command -v docker >/dev/null 2>&1; then
    printf 'unavailable'
    return
  fi
  case "$kind" in
    container)
      docker ps --format '{{.Names}}' | awk -v pattern="$pattern" 'BEGIN {count=0} tolower($0) ~ pattern {count++} END {print count}'
      ;;
    volume)
      docker volume ls --format '{{.Name}}' | awk -v pattern="$pattern" 'BEGIN {count=0} tolower($0) ~ pattern {count++} END {print count}'
      ;;
    image)
      docker images --format '{{.Repository}}:{{.Tag}}' | awk -v pattern="$pattern" 'BEGIN {count=0} tolower($0) ~ pattern {count++} END {print count}'
      ;;
    *)
      printf 'unknown'
      ;;
  esac
}

release_asset_digest() {
  local payload
  if ! command -v gh >/dev/null 2>&1; then
    printf 'gh unavailable'
    return
  fi
  if ! payload="$(gh release view "$RELEASE_TAG" --repo psdjcraw/Kaspa-Node-Watchtower --json assets 2>/dev/null)"; then
    printf 'release unavailable'
    return
  fi
  printf '%s\n' "$payload" | python3 -c '
import json
import sys

version = sys.argv[1]
try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    print("release unavailable")
    raise SystemExit(0)

for asset in payload.get("assets") or []:
    name = asset.get("name") or ""
    if name.startswith(f"kaspa-node-watchtower-{version}-") and name.endswith(".tar.gz"):
        print(asset.get("digest") or "digest missing")
        break
else:
    print("asset missing")
' "$RELEASE_VERSION"
}

release_url() {
  local payload
  if ! command -v gh >/dev/null 2>&1; then
    printf 'gh unavailable'
    return
  fi
  if ! payload="$(gh release view "$RELEASE_TAG" --repo psdjcraw/Kaspa-Node-Watchtower --json url,isDraft,isPrerelease 2>/dev/null)"; then
    printf 'release unavailable'
    return
  fi
  printf '%s\n' "$payload" | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    print("release unavailable")
    raise SystemExit(0)

state = "draft" if payload.get("isDraft") else "published"
kind = "prerelease" if payload.get("isPrerelease") else "stable"
print("{} ({}, {})".format(payload.get("url", "unknown"), state, kind))
'
}

section "Watchtower"
"$PYTHON_BIN" watchtower.py -c config.json --summary

section "Release"
printf 'expected tag: %s\n' "$RELEASE_TAG"
printf 'local version: '
"$PYTHON_BIN" watchtower.py --version
printf 'git head: %s\n' "$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
printf 'release: %s\n' "$(release_url)"
printf 'release asset digest: %s\n' "$(release_asset_digest)"
if command -v gh >/dev/null 2>&1; then
  docker_runs="$(gh run list --repo psdjcraw/Kaspa-Node-Watchtower --workflow docker-publish.yml --limit 5 --json headBranch,status,conclusion,url 2>/dev/null || true)"
  if [ -n "$docker_runs" ]; then
    if printf '%s\n' "$docker_runs" | python3 -c '
import json
import sys

tag = sys.argv[1]
try:
    runs = json.load(sys.stdin)
except json.JSONDecodeError:
    print("docker publish: unavailable")
    raise SystemExit(0)

match = next((run for run in runs if run.get("headBranch") == tag), runs[0] if runs else None)
if not match:
    print("docker publish: missing")
else:
    print(
        "docker publish: "
        "{}/{} {}".format(
            match.get("status", "unknown"),
            match.get("conclusion") or "pending",
            match.get("url", ""),
        ).strip()
    )
' "$RELEASE_TAG"
    then
      true
    else
      printf 'docker publish: unavailable\n'
    fi
  else
    printf 'docker publish: unavailable\n'
  fi
else
  printf 'docker publish: gh unavailable\n'
fi

section "Multi-Node History"
scripts/export_history_sqlite.py --multi-node-summary --days 7 | sed -n '/^window_days=/,$p'

section "Cron Jobs"
cat <<'EOF'
alerts: d370358a-e1f3-4456-9818-68537c558f88 every 10m
benchmarks: aef87796-2552-4cf6-b8ff-897b9ce3ca99 every 30m
smoke: a7e56678-da5c-43dd-8d04-0f3e6e21f1cd daily 03:20 KST
daily-report: c5e0794e-f65f-420b-b07a-4918bef137ae daily 09:10 KST
prometheus-alert-bridge: 919e380f-9a3e-403f-b741-6241d5a60233 every 5m
EOF

section "LaunchAgent"
if launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" >/tmp/kaspa-watchtower-launchd.txt 2>&1; then
  awk '/state =|pid =|path =|program =/ {print}' /tmp/kaspa-watchtower-launchd.txt
else
  printf 'missing: %s\n' "$LAUNCHD_LABEL"
fi

section "Exporter"
printf 'health: '
curl -fsS "$EXPORTER_URL/-/healthy"
printf 'status_ok: %s\n' "$(exporter_metric kaspa_watchtower_status_ok)"
printf 'peer_count: %s\n' "$(exporter_metric kaspa_watchtower_peer_count)"
printf 'latest_relay_age_seconds: %s\n' "$(exporter_metric kaspa_watchtower_latest_relay_age_seconds)"
printf 'latest_processed_tx_rate: %s\n' "$(exporter_metric kaspa_watchtower_latest_processed_transactions_per_second)"
printf 'latest_processed_age_seconds: %s\n' "$(exporter_metric kaspa_watchtower_latest_processed_age_seconds)"
printf 'sync_active: %s\n' "$(exporter_metric kaspa_watchtower_sync_active)"
printf 'sync_baseline_available: %s\n' "$(exporter_metric kaspa_watchtower_sync_baseline_available)"
printf 'sync_daa_rate_per_hour: %s\n' "$(exporter_metric kaspa_watchtower_sync_daa_rate_per_hour)"
printf 'sync_block_rate_per_hour: %s\n' "$(exporter_metric kaspa_watchtower_sync_block_rate_per_hour)"
printf 'sync_header_rate_per_hour: %s\n' "$(exporter_metric kaspa_watchtower_sync_header_rate_per_hour)"
printf 'recovery_executed_total: %s\n' "$(exporter_metric kaspa_watchtower_recovery_executed_total)"
printf 'recovery_dry_runs_total: %s\n' "$(exporter_metric kaspa_watchtower_recovery_dry_runs_total)"
printf 'require_synced: %s\n' "$(exporter_metric kaspa_watchtower_require_synced)"
printf 'require_relay_progress_when_unsynced: %s\n' "$(exporter_metric kaspa_watchtower_require_relay_progress_when_unsynced)"
printf 'require_sync_progress_when_unsynced: %s\n' "$(exporter_metric kaspa_watchtower_require_sync_progress_when_unsynced)"
printf 'sync_progress_stall_minutes: %s\n' "$(exporter_metric kaspa_watchtower_sync_progress_stall_minutes)"

section "Prometheus"
printf 'target status: '
curl -fsS "$PROMETHEUS_URL/api/v1/targets" |
  python3 -c 'import json, sys
data = json.load(sys.stdin)
targets = data.get("data", {}).get("activeTargets", [])
matches = [t for t in targets if t.get("scrapePool") == "kaspa-watchtower"]
print(matches[0].get("health", "missing") if matches else "missing")'
printf 'status_ok query: %s\n' "$(prom_query 'kaspa_watchtower_status_ok')"
printf 'peer_count query: %s\n' "$(prom_query 'kaspa_watchtower_peer_count')"
printf 'latest_relay_age query: %s\n' "$(prom_query 'kaspa_watchtower_latest_relay_age_seconds')"
printf 'latest_processed_tx_rate query: %s\n' "$(prom_query 'kaspa_watchtower_latest_processed_transactions_per_second')"
printf 'latest_processed_age query: %s\n' "$(prom_query 'kaspa_watchtower_latest_processed_age_seconds')"
printf 'sync_active query: %s\n' "$(prom_query 'kaspa_watchtower_sync_active')"
printf 'sync_daa_rate query: %s\n' "$(prom_query 'kaspa_watchtower_sync_daa_rate_per_hour')"
printf 'sync_block_rate query: %s\n' "$(prom_query 'kaspa_watchtower_sync_block_rate_per_hour')"
printf 'sync_header_rate query: %s\n' "$(prom_query 'kaspa_watchtower_sync_header_rate_per_hour')"
printf 'recovery_executed query: %s\n' "$(prom_query 'kaspa_watchtower_recovery_executed_total')"
printf 'recovery_dry_runs query: %s\n' "$(prom_query 'kaspa_watchtower_recovery_dry_runs_total')"
printf 'require_synced query: %s\n' "$(prom_query 'kaspa_watchtower_require_synced')"
printf 'require_relay_progress_when_unsynced query: %s\n' "$(prom_query 'kaspa_watchtower_require_relay_progress_when_unsynced')"
printf 'active alerts: '
curl -fsS "$PROMETHEUS_URL/api/v1/alerts" |
  python3 -c 'import json, sys
data = json.load(sys.stdin)
alerts = [
    item for item in data.get("data", {}).get("alerts", [])
    if item.get("labels", {}).get("service") == "kaspa-watchtower"
]
if not alerts:
    print("0")
else:
    names = [
        f"{item.get('labels', {}).get('alertname', 'unknown')}({item.get('state', 'unknown')})"
        for item in alerts
    ]
    print(f"{len(alerts)} " + ", ".join(names))'

section "Lightweight Indexer"
lightweight_mode="$(exporter_metric kaspa_watchtower_lightweight_mode)"
indexer_enabled="$(exporter_metric kaspa_watchtower_indexer_enabled)"
indexer_watch_enabled="$(exporter_metric kaspa_watchtower_indexer_watch_enabled)"
indexer_containers="$(docker_count_matching container 'simply-kaspa-indexer|kaspa_watchtower_indexer|kaspa_watchtower_db|kaspa-db-data')"
indexer_volumes="$(docker_count_matching volume 'simply-kaspa-indexer|kaspa_watchtower_indexer|kaspa_watchtower_db|kaspa-db-data')"
indexer_images="$(docker_count_matching image 'simply-kaspa-indexer')"
if [ "$lightweight_mode" = "1" ] && [ "$indexer_enabled" = "0" ] && [ "$indexer_watch_enabled" = "0" ] && [ "$indexer_containers" = "0" ] && [ "$indexer_volumes" = "0" ] && [ "$indexer_images" = "0" ]; then
  printf 'verdict: OK lightweight-only; indexer long-term hold intact\n'
else
  printf 'verdict: REVIEW lightweight/indexer posture drift\n'
fi
printf 'lightweight_mode metric: %s\n' "$lightweight_mode"
printf 'indexer_enabled metric: %s\n' "$indexer_enabled"
printf 'indexer_watch_enabled metric: %s\n' "$indexer_watch_enabled"
printf 'indexer containers: %s\n' "$indexer_containers"
printf 'indexer volumes: %s\n' "$indexer_volumes"
printf 'indexer images: %s\n' "$indexer_images"
if command -v docker >/dev/null 2>&1; then
  docker system df
else
  printf 'docker unavailable\n'
fi

section "Grafana"
status="$(curl -s -o /dev/null -w '%{http_code}' "$GRAFANA_URL$GRAFANA_DASHBOARD_PATH")"
printf 'dashboard: %s%s http=%s\n' "$GRAFANA_URL" "$GRAFANA_DASHBOARD_PATH" "$status"

section "GitHub Actions"
if KASPA_WATCHTOWER_GITHUB_WORKFLOW=smoke.yml scripts/check_ci_status.sh; then
  true
else
  printf 'GitHub Actions smoke status unavailable or failing\n'
fi
if KASPA_WATCHTOWER_GITHUB_WORKFLOW=codeql.yml scripts/check_ci_status.sh; then
  true
else
  printf 'GitHub Actions CodeQL status unavailable or failing\n'
fi

section "Recent Files"
ls -lh state/watchtower.prom state/status.html state/benchmarks.jsonl 2>/dev/null
