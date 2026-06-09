#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ACTION="status"
SERVICE="all"
APPLY=0
PLIST_DIR="${KASPA_WATCHTOWER_LAUNCHD_DIR:-$HOME/Library/LaunchAgents}"
DOMAIN="${KASPA_WATCHTOWER_LAUNCHD_DOMAIN:-gui/$(id -u)}"

usage() {
  cat <<'EOF'
Usage: scripts/manage_launchd.sh [--apply] [--service NAME] ACTION

Manage Kaspa Node Watchtower LaunchAgents.

Actions:
  print      Render plist(s) to stdout
  install    Write plist(s), bootstrap if missing, and kickstart
  uninstall  Boot out loaded service(s) and remove installed plist(s)
  restart    Write plist(s), reload loaded service(s), and kickstart
  status     Print launchctl state for service(s)

Options:
  --apply         Run launchctl/copy/remove changes. Without this, install,
                  uninstall, and restart are dry-runs.
  --service NAME  One of: all, exporter, status, benchmark, daily, weekly,
                  alerts, smoke
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --service)
      SERVICE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    print|install|uninstall|restart|status)
      ACTION="$1"
      shift
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

IS_DARWIN=0
if [ "$(uname -s)" = "Darwin" ]; then
  IS_DARWIN=1
fi
if [ "$IS_DARWIN" -eq 0 ] && { [ "$APPLY" -eq 1 ] || [ "$ACTION" = "status" ]; }; then
  printf 'launchd management requires macOS; current_os=%s\n' "$(uname -s)" >&2
  exit 2
fi

service_names() {
  case "$SERVICE" in
    all)
      printf '%s\n' exporter status benchmark daily weekly alerts smoke
      ;;
    exporter|status|benchmark|daily|weekly|alerts|smoke)
      printf '%s\n' "$SERVICE"
      ;;
    *)
      printf 'Unknown service: %s\n' "$SERVICE" >&2
      exit 2
      ;;
  esac
}

label_for() {
  case "$1" in
    exporter) printf 'com.openclaw.kaspa-watchtower-prometheus' ;;
    status) printf 'com.openclaw.kaspa-watchtower-status' ;;
    benchmark) printf 'com.openclaw.kaspa-watchtower-benchmark' ;;
    daily) printf 'com.openclaw.kaspa-watchtower-daily-report' ;;
    weekly) printf 'com.openclaw.kaspa-watchtower-weekly-report' ;;
    alerts) printf 'com.openclaw.kaspa-watchtower-alert-bridge' ;;
    smoke) printf 'com.openclaw.kaspa-watchtower-smoke' ;;
  esac
}

program_for() {
  case "$1" in
    exporter) printf '%s/run_prometheus_exporter.sh' "$PWD" ;;
    status) printf '%s/run_watchtower.sh' "$PWD" ;;
    benchmark) printf '%s/run_benchmark_snapshot.sh' "$PWD" ;;
    daily) printf '%s/run_daily_report.sh' "$PWD" ;;
    weekly) printf '%s/run_weekly_report.sh' "$PWD" ;;
    alerts) printf '%s/scripts/check_prometheus_alerts.sh' "$PWD" ;;
    smoke) printf '%s/run_smoke_test.sh' "$PWD" ;;
  esac
}

stdout_log_for() {
  case "$1" in
    exporter) printf '%s/state/prometheus-exporter.out.log' "$PWD" ;;
    status) printf '%s/state/watchtower-status.out.log' "$PWD" ;;
    benchmark) printf '%s/state/benchmark-snapshot.out.log' "$PWD" ;;
    daily) printf '%s/state/daily-report.out.log' "$PWD" ;;
    weekly) printf '%s/state/weekly-report.out.log' "$PWD" ;;
    alerts) printf '%s/state/prometheus-alert-bridge.out.log' "$PWD" ;;
    smoke) printf '%s/state/smoke-test.out.log' "$PWD" ;;
  esac
}

stderr_log_for() {
  case "$1" in
    exporter) printf '%s/state/prometheus-exporter.err.log' "$PWD" ;;
    status) printf '%s/state/watchtower-status.err.log' "$PWD" ;;
    benchmark) printf '%s/state/benchmark-snapshot.err.log' "$PWD" ;;
    daily) printf '%s/state/daily-report.err.log' "$PWD" ;;
    weekly) printf '%s/state/weekly-report.err.log' "$PWD" ;;
    alerts) printf '%s/state/prometheus-alert-bridge.err.log' "$PWD" ;;
    smoke) printf '%s/state/smoke-test.err.log' "$PWD" ;;
  esac
}

render_schedule() {
  case "$1" in
    exporter)
      cat <<'EOF'
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
EOF
      ;;
    status)
      cat <<'EOF'
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>300</integer>
EOF
      ;;
    benchmark)
      cat <<'EOF'
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>1800</integer>
EOF
      ;;
    daily)
      cat <<'EOF'
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>10</integer>
  </dict>
EOF
      ;;
    weekly)
      cat <<'EOF'
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
EOF
      ;;
    alerts)
      cat <<'EOF'
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>300</integer>
EOF
      ;;
    smoke)
      cat <<'EOF'
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>3</integer>
    <key>Minute</key>
    <integer>20</integer>
  </dict>
EOF
      ;;
  esac
}

render_plist() {
  local name="$1"
  local label program stdout_log stderr_log
  label="$(label_for "$name")"
  program="$(program_for "$name")"
  stdout_log="$(stdout_log_for "$name")"
  stderr_log="$(stderr_log_for "$name")"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$program</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PWD</string>
$(render_schedule "$name")
  <key>StandardOutPath</key>
  <string>$stdout_log</string>
  <key>StandardErrorPath</key>
  <string>$stderr_log</string>
</dict>
</plist>
EOF
}

target_for() {
  printf '%s/%s.plist' "$PLIST_DIR" "$(label_for "$1")"
}

is_loaded() {
  launchctl print "$DOMAIN/$(label_for "$1")" >/dev/null 2>&1
}

write_plist() {
  local name="$1"
  local target
  target="$(target_for "$name")"
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$PLIST_DIR" state
    render_plist "$name" > "$target"
    plutil -lint "$target" >/dev/null
    printf 'wrote=%s\n' "$target"
  else
    printf 'dry_run_write=%s\n' "$target"
  fi
}

install_service() {
  local name="$1"
  local target
  target="$(target_for "$name")"
  write_plist "$name"
  if [ "$APPLY" -eq 0 ]; then
    printf 'dry_run_bootstrap=%s %s\n' "$DOMAIN" "$target"
    printf 'dry_run_kickstart=%s/%s\n' "$DOMAIN" "$(label_for "$name")"
    return 0
  fi
  if is_loaded "$name"; then
    printf 'loaded=%s\n' "$(label_for "$name")"
  else
    launchctl bootstrap "$DOMAIN" "$target"
    printf 'bootstrapped=%s\n' "$(label_for "$name")"
  fi
  launchctl kickstart -k "$DOMAIN/$(label_for "$name")"
  printf 'kickstarted=%s\n' "$(label_for "$name")"
}

restart_service() {
  local name="$1"
  local target
  target="$(target_for "$name")"
  write_plist "$name"
  if [ "$APPLY" -eq 0 ]; then
    printf 'dry_run_bootout=%s/%s\n' "$DOMAIN" "$(label_for "$name")"
    printf 'dry_run_bootstrap=%s %s\n' "$DOMAIN" "$target"
    printf 'dry_run_kickstart=%s/%s\n' "$DOMAIN" "$(label_for "$name")"
    return 0
  fi
  launchctl bootout "$DOMAIN/$(label_for "$name")" >/dev/null 2>&1 || true
  launchctl bootstrap "$DOMAIN" "$target"
  launchctl kickstart -k "$DOMAIN/$(label_for "$name")"
  printf 'restarted=%s\n' "$(label_for "$name")"
}

uninstall_service() {
  local name="$1"
  local target
  target="$(target_for "$name")"
  if [ "$APPLY" -eq 0 ]; then
    printf 'dry_run_bootout=%s/%s\n' "$DOMAIN" "$(label_for "$name")"
    printf 'dry_run_remove=%s\n' "$target"
    return 0
  fi
  launchctl bootout "$DOMAIN/$(label_for "$name")" >/dev/null 2>&1 || true
  rm -f "$target"
  printf 'uninstalled=%s\n' "$(label_for "$name")"
}

status_service() {
  local name="$1"
  local label target
  label="$(label_for "$name")"
  target="$(target_for "$name")"
  printf 'service=%s label=%s plist=%s\n' "$name" "$label" "$target"
  if launchctl print "$DOMAIN/$label" 2>/tmp/kaspa-watchtower-launchd-status.err |
    awk '/state =|pid =|path =|program =|last exit code =/ {print "  " $0}'; then
    return 0
  fi
  printf '  missing_or_unavailable=%s\n' "$label"
  sed -n '1,5p' /tmp/kaspa-watchtower-launchd-status.err | sed 's/^/  /'
}

for name in $(service_names); do
  case "$ACTION" in
    print) render_plist "$name" ;;
    install) install_service "$name" ;;
    restart) restart_service "$name" ;;
    uninstall) uninstall_service "$name" ;;
    status) status_service "$name" ;;
  esac
done
