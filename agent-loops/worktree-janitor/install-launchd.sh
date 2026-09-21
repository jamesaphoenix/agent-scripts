#!/usr/bin/env bash
# Install the worktree janitor as a DAILY per-user launchd agent on the MacBook Pro (08:30 local;
# launchd fires a missed slot on the next wake).
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

LABEL="${WORKTREE_JANITOR_LAUNCHD_LABEL:-com.jud.worktree-janitor}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="${WORKTREE_JANITOR_STATE_DIR:-$ROOT_DIR/state/worktree-janitor}"
HOUR="${WORKTREE_JANITOR_HOUR:-8}"
MINUTE="${WORKTREE_JANITOR_MINUTE:-30}"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"

python3 -c "import json,sys; json.load(open('$ROOT_DIR/config/worktree-janitor.json'))" || { echo "config/worktree-janitor.json is not valid JSON" >&2; exit 1; }
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
    <string>worktree-janitor</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>/usr/bin/env</string>
    <string>python3</string>
    <string>${SCRIPT_DIR}/janitor.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
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
    <key>HOME</key>
    <string>${HOME}</string>
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
echo "  daily:    ${HOUR}:$(printf '%02d' "$MINUTE") local (missed slot fires on next wake)"
echo "  config:   ${ROOT_DIR}/config/worktree-janitor.json"
echo "  report:   ${STATE_DIR}/last-report.json"
echo "  archive:  ${STATE_DIR}/archive/   (diffs + untracked files of removed dirty worktrees)"
echo "  run now:  launchctl kickstart gui/${uid}/${LABEL}"
