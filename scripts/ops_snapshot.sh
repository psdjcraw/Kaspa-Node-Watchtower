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
  curl -fsS "$EXPORTER_URL/metrics" |
    awk -v metric="$metric" '
      $1 ~ ("^" metric "(\\{|$)") {print $2; found=1; exit}
      END {if (!found) print "missing"}
    '
}

section "Watchtower"
"$PYTHON_BIN" watchtower.py -c config.json --summary

section "Cron Jobs"
cat <<'EOF'
alerts: d370358a-e1f3-4456-9818-68537c558f88 every 10m
benchmarks: aef87796-2552-4cf6-b8ff-897b9ce3ca99 every 30m
smoke: a7e56678-da5c-43dd-8d04-0f3e6e21f1cd daily 03:20 KST
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
