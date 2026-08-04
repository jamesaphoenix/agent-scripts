#!/usr/bin/env bash
# Install the host Docker janitor as a cron entry on a Linux host that shares
# a Docker daemon with production but has no launchd (e.g. the Hetzner
# standby box). Deploy by copying this directory to the target host, then
# running this script there as the user that owns the compose stacks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
START_HOUR="${DOCKER_CLEANUP_START_HOUR:-3}"
START_MINUTE="${DOCKER_CLEANUP_START_MINUTE:-30}"
STATE_DIR="${DOCKER_CLEANUP_STATE_DIR:-$SCRIPT_DIR/state}"
CRON_MARKER="# host-docker-cleanup (managed by agent-scripts, do not hand-edit this line)"

mkdir -p "$STATE_DIR/logs"

CRON_LINE="${START_MINUTE} ${START_HOUR} * * * DOCKER_CLEANUP_DISABLE_FILE=${STATE_DIR}/disabled DOCKER_CLEANUP_LOCK_DIR=${STATE_DIR}/lock ${SCRIPT_DIR}/cleanup-local-docker.sh >>${STATE_DIR}/logs/cron.log 2>&1 ${CRON_MARKER}"

existing_crontab="$(crontab -l 2>/dev/null || true)"
filtered_crontab="$(printf '%s\n' "$existing_crontab" | grep -Fv "$CRON_MARKER" || true)"
printf '%s\n%s\n' "$filtered_crontab" "$CRON_LINE" | grep -v '^$' | crontab -

echo "Installed cron entry:"
echo "  $CRON_LINE"
echo "Logs: ${STATE_DIR}/logs/cron.log"
echo "Pause: touch ${STATE_DIR}/disabled"
echo "Resume: rm ${STATE_DIR}/disabled"
echo "Verify: crontab -l"
