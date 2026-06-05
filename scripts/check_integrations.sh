#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

EXPORTER_URL="${KASPA_WATCHTOWER_EXPORTER_URL:-http://127.0.0.1:9660}"
PROMETHEUS_URL="${KASPA_WATCHTOWER_PROMETHEUS_URL:-http://127.0.0.1:9090}"
GRAFANA_URL="${KASPA_WATCHTOWER_GRAFANA_URL:-http://127.0.0.1:3000}"
GRAFANA_DASHBOARD_PATH="${KASPA_WATCHTOWER_GRAFANA_DASHBOARD_PATH:-/d/kaspa-watchtower/kaspa-watchtower}"

ok() {
  printf 'OK %s\n' "$1"
}

check_exporter() {
  curl -fsS "$EXPORTER_URL/-/healthy" >/dev/null
  curl -fsS "$EXPORTER_URL/metrics" | grep -q 'kaspa_watchtower_status_ok'
  ok "exporter metrics endpoint"
}

check_prometheus_query() {
  curl -fsG "$PROMETHEUS_URL/api/v1/query" \
    --data-urlencode 'query=kaspa_watchtower_status_ok' |
    grep -q '"node":"kaspa-tn10-local"'
  ok "Prometheus watchtower status query"
}

check_prometheus_target() {
  curl -fsS "$PROMETHEUS_URL/api/v1/targets" |
    grep -q '"scrapePool":"kaspa-watchtower"'
  curl -fsS "$PROMETHEUS_URL/api/v1/targets" |
    grep -q '"health":"up"'
  ok "Prometheus kaspa-watchtower target"
}

check_prometheus_rules() {
  curl -fsS "$PROMETHEUS_URL/api/v1/rules" |
    grep -q '"name":"kaspa-watchtower"'
  curl -fsS "$PROMETHEUS_URL/api/v1/rules" |
    grep -q '"name":"KaspaWatchtowerCritical"'
  ok "Prometheus watchtower alert rules"
}

check_grafana_dashboard() {
  status="$(curl -s -o /dev/null -w '%{http_code}' "$GRAFANA_URL$GRAFANA_DASHBOARD_PATH")"
  case "$status" in
    200|302)
      ok "Grafana dashboard URL ($status)"
      ;;
    *)
      printf 'FAIL Grafana dashboard URL returned HTTP %s\n' "$status" >&2
      return 1
      ;;
  esac
}

check_github_actions() {
  scripts/check_ci_status.sh >/dev/null
  ok "GitHub Actions latest smoke run"
}

check_exporter
check_prometheus_query
check_prometheus_target
check_prometheus_rules
check_grafana_dashboard
check_github_actions
