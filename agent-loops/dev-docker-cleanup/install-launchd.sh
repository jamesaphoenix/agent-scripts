#!/usr/bin/env bash
# Install the dev Docker janitor as a per-user launchd agent on the MacBook Pro.
# Weekly, Monday 09:30 local. launchd runs a missed StartCalendarInterval on the next
# wake, so a closed lid over the weekend just shifts it, never skips it.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

LABEL="${DEV_DOCKER_CLEANUP_LAUNCHD_LABEL:-com.jud.dev-docker-cleanup}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="${DEV_DOCKER_CLEANUP_STATE_DIR:-$ROOT_DIR/state/dev-docker-cleanup}"
WEEKDAY="${DEV_DOCKER_CLEANUP_WEEKDAY:-1}"
HOUR="${DEV_DOCKER_CLEANUP_HOUR:-9}"
MINUTE="${DEV_DOCKER_CLEANUP_MINUTE:-30}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"

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
    <string>dev-docker-cleanup</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>${SCRIPT_DIR}/cleanup-dev-docker.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>${WEEKDAY}</integer>
    <key>Hour</key>
    <integer>${HOUR}</integer>
    <key>Minute</key>
    <integer>${MINUTE}</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
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
launchctl bootout "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${uid}" "$PLIST_PATH"
launchctl enable "gui/${uid}/${LABEL}"

echo "Installed ${LABEL}"
echo "  plist:    ${PLIST_PATH}"
echo "  schedule: weekday ${WEEKDAY} at ${HOUR}:$(printf '%02d' "$MINUTE") local (missed runs fire on next wake)"
echo "  state:    ${STATE_DIR}/last-summary.json"
echo "  logs:     ${STATE_DIR}/logs/"
echo "  run now:  launchctl kickstart gui/${uid}/${LABEL}"
