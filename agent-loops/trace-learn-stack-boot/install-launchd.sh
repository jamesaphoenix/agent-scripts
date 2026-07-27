#!/usr/bin/env bash
# Install the Trace Learn stack bring-up LaunchAgent on THIS machine.
#
# Run on the Mac Studio only (that is where the compose stacks live). Authoring happens
# on the MacBook per the root CLAUDE.md; this is the deploy step.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${TRACE_LEARN_STACK_BOOT_LAUNCHD_LABEL:-com.jud.trace-learn-stack-boot}"
# Re-checks every 5 min. RunAtLoad alone is not enough: Docker Desktop starts at user
# LOGIN and takes time to become usable, so the first run can land before the daemon is
# ready. The script is idempotent and silent when there is nothing to do.
INTERVAL_SECONDS="${TRACE_LEARN_STACK_BOOT_INTERVAL_SECONDS:-300}"
STATE_DIR="${TRACE_LEARN_STACK_BOOT_STATE_DIR:-$ROOT_DIR/state/trace-learn-stack-boot}"
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
    <string>trace-learn-stack-boot</string>
    <string>--state-dir</string>
    <string>${STATE_DIR}</string>
    <string>--cwd</string>
    <string>${ROOT_DIR}</string>
    <string>--</string>
    <string>${SCRIPT_DIR}/up.sh</string>
  </array>
  <key>StartInterval</key>
  <integer>${INTERVAL_SECONDS}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${STATE_DIR}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_DIR}/logs/launchd.err.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "Installed ${LABEL}"
echo "  plist:  ${PLIST_PATH}"
echo "  logs:   ${STATE_DIR}/logs/"
echo "  pause:  touch ${STATE_DIR}/disabled     (holds ALL stacks down for maintenance)"
echo "  resume: rm ${STATE_DIR}/disabled"
