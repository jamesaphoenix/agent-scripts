#!/usr/bin/env bash
# Install the Docker wedge detector + recovery LaunchAgent on THIS machine.
#
# Run on the Mac Studio only (that is where the wedging Docker Desktop and the
# production stacks live). Authoring happens on the MacBook per the root
# CLAUDE.md; this is the deploy step. check.sh additionally refuses to reboot
# anything that is not an allowed host/user, so a stray install elsewhere still
# cannot take that machine down.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${DOCKER_WEDGE_LAUNCHD_LABEL:-com.jud.docker-wedge-recovery}"
# Every 10 minutes. The probe is cheap when healthy (a container start that
# exits immediately) and the wedge is not urgent to the minute - what matters
# is that it is caught within an hour rather than the next morning.
INTERVAL_SECONDS="${DOCKER_WEDGE_INTERVAL_SECONDS:-600}"
STATE_DIR="${DOCKER_WEDGE_STATE_DIR:-$ROOT_DIR/state/docker-wedge-recovery}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"

if ! [[ "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || (( INTERVAL_SECONDS < 60 )); then
  echo "DOCKER_WEDGE_INTERVAL_SECONDS must be an integer of at least 60, got '$INTERVAL_SECONDS'."
  exit 1
fi

mkdir -p "$PLIST_DIR" "$STATE_DIR/logs"

# NOT RunAtLoad. This agent loads at LOGIN, which is exactly when the machine
# has just booted and Docker Desktop is still starting - the one moment a
# reboot decision would be least informed. check.sh guards that case too, but
# not scheduling it there is cheaper than relying on the guard.
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
    <string>docker-wedge-recovery</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>${SCRIPT_DIR}/check.sh</string>
  </array>
  <key>StartInterval</key>
  <integer>${INTERVAL_SECONDS}</integer>
  <key>StandardOutPath</key>
  <string>${STATE_DIR}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_DIR}/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>DOCKER_WEDGE_STATE_DIR</key>
    <string>${STATE_DIR}</string>
    <key>DOCKER_WEDGE_DISABLE_FILE</key>
    <string>${STATE_DIR}/disabled</string>
  </dict>
</dict>
</plist>
PLIST

plutil -lint "$PLIST_PATH"

uid="$(id -u)"
launchctl bootout "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${uid}" "$PLIST_PATH"
launchctl enable "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true

cat <<EOF
Installed ${LABEL}
Plist: ${PLIST_PATH}
Schedule: every ${INTERVAL_SECONDS}s (no RunAtLoad, on purpose)
State: ${STATE_DIR}
Logs: ${STATE_DIR}/logs/
Pause: touch ${STATE_DIR}/disabled
Resume: rm ${STATE_DIR}/disabled

Inspect:
  launchctl print "gui/${uid}/${LABEL}"
Run now (WILL reboot this host if it is wedged and within rate limits):
  launchctl kickstart -k "gui/${uid}/${LABEL}"
Probe only, never reboots:
  DOCKER_WEDGE_ALLOW_REBOOT_HOSTS=none ${SCRIPT_DIR}/check.sh
EOF
