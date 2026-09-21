#!/usr/bin/env bash
# Install the temporal-worker-tailscale-health LaunchAgent on THIS machine.
#
# Run on the Mac Studio only. Normally invoked through
# scripts/install-launchd-tasks.sh --task temporal-worker-tailscale-health.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${TEMPORAL_HEALTH_LAUNCHD_LABEL:-com.jud.temporal-worker-tailscale-health}"
# Every minute. A stale route means every worker on the host stops polling, so a fast
# detect-and-repair matters more than the (tiny) cost of a few TCP connects. Repairs
# themselves are rate-limited inside check.sh.
INTERVAL_SECONDS="${TEMPORAL_HEALTH_INTERVAL_SECONDS:-60}"
STATE_DIR="${TEMPORAL_HEALTH_STATE_DIR:-$ROOT_DIR/state/temporal-worker-tailscale-health}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"
# Labels this loop replaces. Booted out and removed so two agents never both repair.
LEGACY_LABELS=(com.octospark.temporal-route-health)

python3 "$ROOT_DIR/lib/docker-apps.py" validate

mkdir -p "$PLIST_DIR" "$STATE_DIR/logs"

cat >"$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${ROOT_DIR}/lib/run-job.sh</string>
    <string>--id</string>
    <string>temporal-worker-tailscale-health</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>${SCRIPT_DIR}/check.sh</string>
  </array>
  <key>StartInterval</key>
  <integer>${INTERVAL_SECONDS}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${STATE_DIR}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_DIR}/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
PLIST

uid="$(id -u)"
for legacy in "${LEGACY_LABELS[@]}"; do
  if launchctl print "gui/${uid}/${legacy}" >/dev/null 2>&1; then
    launchctl bootout "gui/${uid}/${legacy}" >/dev/null 2>&1 || true
    echo "Removed legacy agent ${legacy}"
  fi
  rm -f "$PLIST_DIR/${legacy}.plist"
done

launchctl bootout "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${uid}" "$PLIST_PATH"
launchctl kickstart "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true

echo "Installed ${LABEL}"
echo "  plist:  ${PLIST_PATH}"
echo "  every:  ${INTERVAL_SECONDS}s plus RunAtLoad"
echo "  apps:   ${ROOT_DIR}/config/docker-apps.json (temporal.enabled entries)"
echo "  state:  ${STATE_DIR}/last-status.json"
echo "  logs:   ${STATE_DIR}/logs/"
