#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${OCTOSPARK_TEMPORAL_ROUTE_LAUNCHD_LABEL:-com.octospark.temporal-route-health}"
INTERVAL_SECONDS="${OCTOSPARK_TEMPORAL_ROUTE_INTERVAL_SECONDS:-300}"
STATE_DIR="${OCTOSPARK_TEMPORAL_ROUTE_STATE_DIR:-$ROOT_DIR/state/octospark-temporal-route-health}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"
TIMEOUT_ARGUMENTS=""

if command -v timeout >/dev/null 2>&1 || command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_ARGUMENTS="    <string>--timeout-seconds</string>
    <string>90</string>"
fi

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
    <string>octospark-temporal-route-health</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
${TIMEOUT_ARGUMENTS}
    <string>--</string>
    <string>${SCRIPT_DIR}/check.sh</string>
  </array>
  <key>StartInterval</key>
  <integer>${INTERVAL_SECONDS}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${STATE_DIR}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_DIR}/launchd.err.log</string>
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
launchctl kickstart "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true

echo "Installed ${LABEL} at ${PLIST_PATH}"
echo "State: ${STATE_DIR}"
