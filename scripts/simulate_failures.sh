#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

if [ ! -f "config.json" ]; then
  printf 'FAIL config.json is required for live-data simulations\n' >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

make_config() {
  local name="$1"
  local output="$2"
  local state_path="${3:-$TMP_DIR/$name-state.json}"
  python3 - "$name" "config.json" "$output" "$state_path" "$TMP_DIR" <<'PY'
import json
import sys

name, source, output, state_path, tmp_dir = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    config = json.load(handle)

config["state_path"] = state_path
config["status_page_path"] = f"{tmp_dir}/{name}-status.html"
config["canvas_status_page_path"] = ""
config["benchmark_path"] = f"{tmp_dir}/{name}-benchmarks.jsonl"
config["prometheus_metrics_path"] = f"{tmp_dir}/{name}.prom"
config["recovery_history_path"] = f"{tmp_dir}/{name}-recovery-history.jsonl"
config.setdefault("thresholds", {})
config["thresholds"]["alert_repeat_minutes"] = 60

if name == "peer-critical":
    config["thresholds"]["min_peer_count"] = 999
elif name == "relay-warning":
    config["thresholds"]["min_relay_blocks_in_window"] = 999999
elif name == "rpc-critical":
    config["rpc_endpoint"] = "127.0.0.1:1"
elif name == "peer-recovered":
    config["thresholds"]["min_peer_count"] = 1
else:
    raise SystemExit(f"unknown simulation: {name}")

with open(output, "w", encoding="utf-8") as handle:
    json.dump(config, handle)
PY
}

assert_contains() {
  local text="$1"
  local pattern="$2"
  if ! grep -Fq "$pattern" <<<"$text"; then
    printf 'FAIL expected pattern not found: %s\n' "$pattern" >&2
    printf '%s\n' "$text" >&2
    exit 1
  fi
}

run_alert_case() {
  local name="$1"
  local expected="$2"
  shift 2
  local config_path="$TMP_DIR/$name.json"
  make_config "$name" "$config_path"
  set +e
  local output
  output="$("$PYTHON_BIN" watchtower.py -c "$config_path" --alert 2>&1)"
  local status=$?
  set -e
  if [ "$status" -ne "$expected" ]; then
    printf 'FAIL %s expected exit %s got %s\n' "$name" "$expected" "$status" >&2
    printf '%s\n' "$output" >&2
    exit 1
  fi
  for pattern in "$@"; do
    assert_contains "$output" "$pattern"
  done
  printf 'OK %s\n' "$name"
}

run_alert_case "peer-critical" 1 "critical" "peer_count"
run_alert_case "relay-warning" 1 "warning" "block_progress"
run_alert_case "rpc-critical" 1 "critical" "rpc_tcp"

peer_config="$TMP_DIR/repeat-peer-critical.json"
peer_state="$TMP_DIR/repeat-peer-critical-state.json"
make_config "peer-critical" "$peer_config" "$peer_state"
set +e
first_output="$("$PYTHON_BIN" watchtower.py -c "$peer_config" --alert 2>&1)"
first_status=$?
second_output="$("$PYTHON_BIN" watchtower.py -c "$peer_config" --alert 2>&1)"
second_status=$?
set -e
if [ "$first_status" -ne 1 ] || [ "$second_status" -ne 1 ]; then
  printf 'FAIL repeat suppression expected alert exits to be 1/1 got %s/%s\n' "$first_status" "$second_status" >&2
  exit 1
fi
assert_contains "$first_output" "critical"
if [ -n "$second_output" ]; then
  printf 'FAIL repeat suppression expected second output to be empty\n%s\n' "$second_output" >&2
  exit 1
fi
printf 'OK repeat suppression\n'

recovered_config="$TMP_DIR/peer-recovered.json"
make_config "peer-recovered" "$recovered_config" "$peer_state"
set +e
recovered_output="$("$PYTHON_BIN" watchtower.py -c "$recovered_config" --alert 2>&1)"
recovered_status=$?
set -e
if [ "$recovered_status" -ne 0 ]; then
  printf 'FAIL recovered transition expected exit 0 got %s\n%s\n' "$recovered_status" "$recovered_output" >&2
  exit 1
fi
assert_contains "$recovered_output" "recovered"
printf 'OK recovered transition\n'

set +e
recovery_output="$("$PYTHON_BIN" watchtower.py -c "$peer_config" --recover --dry-run 2>&1)"
recovery_status=$?
set -e
if [ "$recovery_status" -ne 0 ]; then
  printf 'FAIL recovery dry-run expected exit 0 got %s\n%s\n' "$recovery_status" "$recovery_output" >&2
  exit 1
fi
assert_contains "$recovery_output" "Recovery command:"
assert_contains "$recovery_output" "Recovery dry-run"
printf 'OK recovery dry-run\n'
