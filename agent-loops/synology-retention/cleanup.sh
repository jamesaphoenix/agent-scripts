#!/bin/sh
# NAS-local entrypoint. No SSH, Mac, network scheduler or credentials required.
set -eu
umask 077
PATH=/usr/local/bin:/usr/bin:/bin
export PATH
STATE=/volume1/docker/jud/maintenance
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "${1:---apply}" in
  --apply) MODE=--apply ;;
  --observe) MODE= ;;
  *) echo 'Usage: cleanup.sh [--apply|--observe]' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || exit 2
if [ -e "$STATE/cleanup-disabled" ]; then
  echo '{"skipped":"NAS cleanup disabled"}'
  exit 0
fi
# TERM ends the whole process group; KILL bounds even a stuck Docker child.
# retention-last.json is replaced each run, so no append-only cleanup log grows.
exec /usr/bin/timeout --kill-after=15s 420s /usr/bin/python3 "$SCRIPT_DIR/retention.py" ${MODE:+"$MODE"}
