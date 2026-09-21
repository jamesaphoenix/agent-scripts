#!/usr/bin/env bash
# Install the CI VM janitor LaunchAgent on THIS machine.
#
# Mac Studio only: that is where the Colima "ci" VM and the self-hosted
# GitHub Actions runners live. Authoring happens on the MacBook per the root
# CLAUDE.md; this is the deploy step.
#
# Scheduled after com.jud.host-docker-cleanup (03:30) so the two janitors
# never contend for the same Docker daemon or the boot volume.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${CI_VM_CLEANUP_LAUNCHD_LABEL:-com.jud.ci-vm-cleanup}"
START_HOUR="${CI_VM_CLEANUP_START_HOUR:-4}"
START_MINUTE="${CI_VM_CLEANUP_START_MINUTE:-10}"
STATE_DIR="${CI_VM_CLEANUP_STATE_DIR:-$ROOT_DIR/state/ci-vm-cleanup}"
COLIMA_PROFILE="${CI_VM_CLEANUP_PROFILE:-ci}"
ORPHAN_PATTERNS="${CI_VM_CLEANUP_ORPHAN_PATTERNS:-tracelearn-mobile-readiness-}"
ORPHAN_MIN_AGE_HOURS="${CI_VM_CLEANUP_ORPHAN_MIN_AGE_HOURS:-3}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"

if ! [[ "$START_HOUR" =~ ^[0-9]+$ ]] || (( START_HOUR < 0 || START_HOUR > 23 )); then
  echo "CI_VM_CLEANUP_START_HOUR must be an integer from 0 to 23, got '$START_HOUR'."
  exit 1
fi

if ! [[ "$START_MINUTE" =~ ^[0-9]+$ ]] || (( START_MINUTE < 0 || START_MINUTE > 59 )); then
  echo "CI_VM_CLEANUP_START_MINUTE must be an integer from 0 to 59, got '$START_MINUTE'."
  exit 1
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
    <string>ci-vm-cleanup</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>${SCRIPT_DIR}/cleanup-ci-vm.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${START_HOUR}</integer>
    <key>Minute</key>
    <integer>${START_MINUTE}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${STATE_DIR}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_DIR}/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>CI_VM_CLEANUP_PROFILE</key>
    <string>${COLIMA_PROFILE}</string>
    <key>CI_VM_CLEANUP_ORPHAN_PATTERNS</key>
    <string>${ORPHAN_PATTERNS}</string>
    <key>CI_VM_CLEANUP_ORPHAN_MIN_AGE_HOURS</key>
    <string>${ORPHAN_MIN_AGE_HOURS}</string>
    <key>CI_VM_CLEANUP_DISABLE_FILE</key>
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
Schedule: daily at $(printf '%02d:%02d' "$START_HOUR" "$START_MINUTE")
Logs: ${STATE_DIR}/logs/
Colima profile: ${COLIMA_PROFILE}
Orphan patterns: ${ORPHAN_PATTERNS} (min age ${ORPHAN_MIN_AGE_HOURS}h)
Pause: touch ${STATE_DIR}/disabled
Resume: rm ${STATE_DIR}/disabled

Inspect:
  launchctl print "gui/${uid}/${LABEL}"
Run now:
  launchctl kickstart -k "gui/${uid}/${LABEL}"
Dry run (manual):
  DRY_RUN=1 ${SCRIPT_DIR}/cleanup-ci-vm.sh
EOF
