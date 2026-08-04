#!/usr/bin/env bash
# Install the host Docker janitor LaunchAgent on THIS machine.
#
# Run on the Mac Studio only (that is where the CI runners + production Docker
# daemon live). Authoring happens on the MacBook per the root CLAUDE.md; this
# is the deploy step.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${DOCKER_CLEANUP_LAUNCHD_LABEL:-com.jud.host-docker-cleanup}"
START_HOUR="${DOCKER_CLEANUP_START_HOUR:-3}"
START_MINUTE="${DOCKER_CLEANUP_START_MINUTE:-30}"
STATE_DIR="${DOCKER_CLEANUP_STATE_DIR:-$ROOT_DIR/state/docker-cleanup}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"

# Every CI-runner working copy of every product repo on this host, so the
# janitor sees each product's recent-release artifact history. Colon-separated
# per cleanup-local-docker.sh's DOCKER_CLEANUP_ARTIFACT_DIRS contract. Missing
# directories are skipped silently (a runner slot that has never released
# yet), so it's safe to list all four slots for both products up front.
DEFAULT_ARTIFACT_DIRS=""
for n in 1 2 3 4; do
  for product in octospark trace-learn; do
    dir="$HOME/actions-runner-${n}/_work${n}/${product}/${product}/deploy/artifacts"
    DEFAULT_ARTIFACT_DIRS="${DEFAULT_ARTIFACT_DIRS:+${DEFAULT_ARTIFACT_DIRS}:}${dir}"
  done
done
DOCKER_CLEANUP_ARTIFACT_DIRS="${DOCKER_CLEANUP_ARTIFACT_DIRS:-$DEFAULT_ARTIFACT_DIRS}"

if ! [[ "$START_HOUR" =~ ^[0-9]+$ ]] || (( START_HOUR < 0 || START_HOUR > 23 )); then
  echo "DOCKER_CLEANUP_START_HOUR must be an integer from 0 to 23, got '$START_HOUR'."
  exit 1
fi

if ! [[ "$START_MINUTE" =~ ^[0-9]+$ ]] || (( START_MINUTE < 0 || START_MINUTE > 59 )); then
  echo "DOCKER_CLEANUP_START_MINUTE must be an integer from 0 to 59, got '$START_MINUTE'."
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
    <string>docker-cleanup</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>${SCRIPT_DIR}/cleanup-local-docker.sh</string>
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
    <key>DOCKER_CLEANUP_ARTIFACT_DIRS</key>
    <string>${DOCKER_CLEANUP_ARTIFACT_DIRS}</string>
    <key>DOCKER_CLEANUP_DISABLE_FILE</key>
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
Protecting artifact dirs: ${DOCKER_CLEANUP_ARTIFACT_DIRS}
Pause: touch ${STATE_DIR}/disabled
Resume: rm ${STATE_DIR}/disabled

Inspect:
  launchctl print "gui/${uid}/${LABEL}"
Run now:
  launchctl kickstart -k "gui/${uid}/${LABEL}"
Dry run (manual):
  DRY_RUN=1 ${SCRIPT_DIR}/cleanup-local-docker.sh
EOF
