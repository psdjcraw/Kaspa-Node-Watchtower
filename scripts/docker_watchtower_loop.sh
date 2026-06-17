#!/usr/bin/env sh
set -eu

CONFIG_PATH="${WATCHTOWER_CONFIG:-/config/config.json}"
INTERVAL_SECONDS="${WATCHTOWER_INTERVAL_SECONDS:-300}"

while :; do
  python /app/watchtower.py -c "$CONFIG_PATH" --alert || true
  sleep "$INTERVAL_SECONDS"
done
