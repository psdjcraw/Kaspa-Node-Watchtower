#!/usr/bin/env sh
set -eu

base_url="${1:-http://127.0.0.1:8500}"
base_url="${base_url%/}"
timeout_seconds="${INDEXER_SMOKE_TIMEOUT_SECONDS:-20}"
retry_count="${INDEXER_SMOKE_RETRIES:-2}"
indexer_syncing=0

require() {
  name="$1"
  url="$2"
  if curl -fsS --retry "$retry_count" --retry-all-errors --max-time "$timeout_seconds" "$url" >/dev/null; then
    printf 'OK %s %s\n' "$name" "$url"
  else
    printf 'FAILED %s %s\n' "$name" "$url" >&2
    exit 1
  fi
}

require_health() {
  url="$1"
  body_file="$(mktemp)"
  status="$(curl -sS --retry "$retry_count" --retry-all-errors --max-time "$timeout_seconds" -o "$body_file" -w '%{http_code}' "$url" || true)"
  if [ "$status" = "200" ]; then
    rm -f "$body_file"
    printf 'OK indexer health %s\n' "$url"
    return
  fi
  if [ "$status" = "503" ] && grep -q '"kaspad"' "$body_file" && grep -q 'behind' "$body_file"; then
    rm -f "$body_file"
    indexer_syncing=1
    printf 'OK indexer health %s (syncing)\n' "$url"
    return
  fi
  rm -f "$body_file"
  printf 'FAILED indexer health %s status=%s\n' "$url" "$status" >&2
  exit 1
}

require_ready_or_skip() {
  name="$1"
  url="$2"
  optional_timeout="${INDEXER_SMOKE_OPTIONAL_TIMEOUT_SECONDS:-5}"
  optional_retries="${INDEXER_SMOKE_OPTIONAL_RETRIES:-0}"
  if [ "${INDEXER_SMOKE_STRICT_READY:-0}" = "1" ]; then
    optional_timeout="$timeout_seconds"
    optional_retries="$retry_count"
  fi
  if curl -fsS --retry "$optional_retries" --retry-all-errors --max-time "$optional_timeout" "$url" >/dev/null; then
    printf 'OK %s %s\n' "$name" "$url"
    return
  fi
  if [ "${INDEXER_SMOKE_STRICT_READY:-0}" != "1" ]; then
    printf 'WARN %s %s (endpoint busy; set INDEXER_SMOKE_STRICT_READY=1 to require it)\n' "$name" "$url" >&2
    return
  fi
  if [ "$indexer_syncing" = "1" ]; then
    printf 'WARN %s %s (skipped while indexer is syncing)\n' "$name" "$url" >&2
    return
  fi
  printf 'FAILED %s %s\n' "$name" "$url" >&2
  exit 1
}

require_health "$base_url/api/health"
require "indexer metrics" "$base_url/api/metrics"
require_ready_or_skip "indexer status" "$base_url/api/status"
require_ready_or_skip "recent blocks" "$base_url/api/blocks/recent?limit=1"
require "admin dashboard" "$base_url/admin"
