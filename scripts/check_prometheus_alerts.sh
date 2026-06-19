#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PROMETHEUS_URL="${KASPA_WATCHTOWER_PROMETHEUS_URL:-http://127.0.0.1:9090}"
STATE_PATH="${KASPA_WATCHTOWER_PROMETHEUS_ALERT_STATE:-state/prometheus-alert-state.json}"

if [ -n "${KASPA_WATCHTOWER_PROMETHEUS_ALERTS_FILE:-}" ]; then
  response="$(cat "$KASPA_WATCHTOWER_PROMETHEUS_ALERTS_FILE")"
else
  if ! response="$(curl -fsS "$PROMETHEUS_URL/api/v1/alerts")"; then
    printf 'Kaspa Prometheus alert bridge failed: cannot query %s/api/v1/alerts\n' "$PROMETHEUS_URL" >&2
    exit 1
  fi
fi

RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT
printf '%s' "$response" >"$RESPONSE_FILE"

python3 - "$STATE_PATH" "$RESPONSE_FILE" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
response_path = Path(sys.argv[2])
with response_path.open(encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("status") != "success":
    print("Kaspa Prometheus alert bridge failed: Prometheus API returned non-success status")
    raise SystemExit(1)

alerts_by_fingerprint = {}
for alert in data.get("data", {}).get("alerts", []):
    labels = alert.get("labels") or {}
    if labels.get("service") != "kaspa-watchtower":
        continue
    if alert.get("state") not in {"pending", "firing"}:
        continue
    annotations = alert.get("annotations") or {}
    item = {
        "state": alert.get("state", "unknown"),
        "alertname": labels.get("alertname", "unknown"),
        "node": labels.get("node", labels.get("instance", "unknown")),
        "severity": labels.get("severity", "unknown"),
        "summary": annotations.get("summary", ""),
        "description": annotations.get("description", ""),
    }
    fingerprint = f"{item['state']}:{item['alertname']}:{item['node']}:{item['severity']}"
    alerts_by_fingerprint[fingerprint] = item

fingerprints = sorted(alerts_by_fingerprint)
alerts = [alerts_by_fingerprint[fingerprint] for fingerprint in fingerprints]

previous = {}
if state_path.exists():
    try:
        with state_path.open(encoding="utf-8") as handle:
            previous = json.load(handle)
    except (OSError, json.JSONDecodeError):
        previous = {}
previous_fingerprints = sorted(set(previous.get("fingerprints") or []))
previous_set = set(previous_fingerprints)
current_set = set(fingerprints)
new_fingerprints = sorted(current_set - previous_set)
resolved_fingerprints = sorted(previous_set - current_set)

state_path.parent.mkdir(parents=True, exist_ok=True)
with state_path.open("w", encoding="utf-8") as handle:
    json.dump(
        {
            "checked_at": dt.datetime.now().astimezone().isoformat(),
            "fingerprints": fingerprints,
            "alerts": alerts,
        },
        handle,
        indent=2,
        sort_keys=True,
    )
    handle.write("\n")

if not fingerprints:
    if previous_fingerprints:
        print("Kaspa Prometheus alerts recovered")
        print(f"previous_alerts={len(previous_fingerprints)}")
        if resolved_fingerprints:
            print("resolved:")
            for fingerprint in resolved_fingerprints:
                print(f"- {fingerprint}")
    raise SystemExit(0)

if fingerprints == previous_fingerprints:
    raise SystemExit(0)

print("Kaspa Prometheus alerts changed")
print(f"active_alerts={len(fingerprints)} new_alerts={len(new_fingerprints)} resolved_alerts={len(resolved_fingerprints)}")
if resolved_fingerprints:
    print("resolved:")
    for fingerprint in resolved_fingerprints:
        print(f"- {fingerprint}")

items_to_print = [alerts_by_fingerprint[fingerprint] for fingerprint in new_fingerprints] or alerts
print("active:")
for item in items_to_print:
    detail = item["description"] or item["summary"] or "no detail"
    print(
        f"- {item['state']} {item['severity']} {item['alertname']} "
        f"node={item['node']}: {detail}"
    )
PY
