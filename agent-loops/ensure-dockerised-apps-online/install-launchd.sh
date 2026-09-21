#!/usr/bin/env bash
# Install the ensure-dockerised-apps-online LaunchAgent on THIS machine.
#
# Run on the Mac Studio only (that is where the compose stacks live). Authoring happens
# on the MacBook per the root README; this is the deploy step, normally invoked through
# scripts/install-launchd-tasks.sh --task ensure-dockerised-apps-online.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${ENSURE_DOCKER_APPS_LAUNCHD_LABEL:-com.jud.ensure-dockerised-apps-online}"
# Re-checks every 5 min. RunAtLoad alone is not enough: Docker Desktop starts at user
# LOGIN and takes time to become usable, so the first run can land before the daemon is
# ready. The script is idempotent and silent when there is nothing to do.
INTERVAL_SECONDS="${ENSURE_DOCKER_APPS_INTERVAL_SECONDS:-300}"
STATE_DIR="${ENSURE_DOCKER_APPS_STATE_DIR:-$ROOT_DIR/state/ensure-dockerised-apps-online}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"
# Labels this loop replaces. Booted out and removed so two agents never fight over the
# same containers.
LEGACY_LABELS=(com.jud.trace-learn-stack-boot)

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
    <string>ensure-dockerised-apps-online</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>${SCRIPT_DIR}/ensure-online.sh</string>
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
echo "  apps:   ${ROOT_DIR}/config/docker-apps.json"
echo "  logs:   ${STATE_DIR}/logs/"
echo "  pause:  touch ${STATE_DIR}/disabled            (holds ALL apps down)"
echo "          touch ${STATE_DIR}/disabled.<app-id>   (holds one app down)"
