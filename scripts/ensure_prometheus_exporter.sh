#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LABEL="com.openclaw.kaspa-watchtower-prometheus"
DOMAIN="gui/$(id -u)"
PLIST_SOURCE="$PWD/launchd/$LABEL.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
EXPORTER_URL="${KASPA_WATCHTOWER_EXPORTER_URL:-http://127.0.0.1:9660}"
CONFIG="${CONFIG:-config.json}"

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

log() {
  printf '%s\n' "$1"
}

install_plist() {
  mkdir -p "$HOME/Library/LaunchAgents"
  if [ ! -f "$PLIST_TARGET" ] || ! cmp -s "$PLIST_SOURCE" "$PLIST_TARGET"; then
    cp "$PLIST_SOURCE" "$PLIST_TARGET"
    log "installed_plist=$PLIST_TARGET"
    return 0
  fi
  log "installed_plist=unchanged"
  return 1
}

bootstrap_service() {
  local changed="$1"
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    log "launchagent=loaded"
    if [ "$changed" = "1" ]; then
      launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
      launchctl bootstrap "$DOMAIN" "$PLIST_TARGET"
      log "launchagent=reloaded"
    fi
  else
    launchctl bootstrap "$DOMAIN" "$PLIST_TARGET"
    log "launchagent=bootstrapped"
  fi
  launchctl kickstart -k "$DOMAIN/$LABEL"
  log "launchagent=kickstarted"
}

wait_for_exporter() {
  local attempt
  for attempt in 1 2 3 4 5; do
    if curl -fsS "$EXPORTER_URL/-/healthy" >/dev/null 2>&1; then
      log "exporter_health=ok"
      return 0
    fi
    sleep 1
  done
  log "exporter_health=failed"
  return 1
}

changed=0
if install_plist; then
  changed=1
fi

"$PYTHON_BIN" watchtower.py -c "$CONFIG" --prometheus >/dev/null
bootstrap_service "$changed"
wait_for_exporter
curl -fsS "$EXPORTER_URL/metrics" | grep -q 'kaspa_watchtower_status_ok'
log "exporter_metrics=ok"
