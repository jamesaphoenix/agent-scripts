#!/bin/bash
# Install the Playwright CLI reaper as an hourly per-user launchd agent.
# Registered for both Macs (see launchd-tasks/registry.json): agents on either host leave
# detached playwright-cli daemons and headless Chrome trees running for hours.
set -euo pipefail

LABEL="com.jud.playwright-cli-reaper"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${SCRIPT_DIR}/reap.sh"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
MAX_AGE_HOURS="${PLAYWRIGHT_REAPER_MAX_AGE_HOURS:-3}"

mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${SCRIPT}</string>
        <string>--max-age-hours</string>
        <string>${MAX_AGE_HOURS}</string>
    </array>
    <key>StartInterval</key><integer>3600</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>${HOME}/Library/Logs/playwright-cli-reaper.log</string>
    <key>StandardErrorPath</key><string>${HOME}/Library/Logs/playwright-cli-reaper.err.log</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/${LABEL}"
echo "Installed ${LABEL} (hourly + RunAtLoad, daemons older than ${MAX_AGE_HOURS}h)"
