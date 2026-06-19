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
  if ! curl -fsS "$EXPORTER_URL/-/healthy" >/dev/null; then
    printf 'FAIL exporter health endpoint unavailable: %s/-/healthy\n' "$EXPORTER_URL" >&2
    return 1
  fi
  metrics="$(curl -fsS "$EXPORTER_URL/metrics")"
  if ! grep -q 'kaspa_watchtower_status_ok' <<<"$metrics"; then
    printf 'FAIL exporter metrics missing kaspa_watchtower_status_ok: %s/metrics\n' "$EXPORTER_URL" >&2
    return 1
  fi
  ok "exporter metrics endpoint"
}

check_prometheus_query() {
  response="$(curl -fsG "$PROMETHEUS_URL/api/v1/query" \
    --data-urlencode 'query=kaspa_watchtower_status_ok')"
  grep -q '"node":"kaspa-mainnet-local"' <<<"$response"
  ok "Prometheus watchtower status query"
}

check_prometheus_target() {
  response="$(curl -fsS "$PROMETHEUS_URL/api/v1/targets")"
  grep -q '"scrapePool":"kaspa-watchtower"' <<<"$response"
  grep -q '"health":"up"' <<<"$response"
  ok "Prometheus kaspa-watchtower target"
}

check_prometheus_rules() {
  response="$(curl -fsS "$PROMETHEUS_URL/api/v1/rules")"
  grep -q '"name":"kaspa-watchtower"' <<<"$response"
  grep -q '"name":"KaspaWatchtowerCritical"' <<<"$response"
  grep -q '"name":"KaspaWatchtowerRecoveryCommandFailed"' <<<"$response"
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
  KASPA_WATCHTOWER_GITHUB_WORKFLOW=smoke.yml scripts/check_ci_status.sh >/dev/null
  ok "GitHub Actions latest smoke run"
  KASPA_WATCHTOWER_GITHUB_WORKFLOW=codeql.yml scripts/check_ci_status.sh >/dev/null
  ok "GitHub Actions latest CodeQL run"
}

check_exporter
check_prometheus_query
check_prometheus_target
check_prometheus_rules
check_grafana_dashboard
check_github_actions
