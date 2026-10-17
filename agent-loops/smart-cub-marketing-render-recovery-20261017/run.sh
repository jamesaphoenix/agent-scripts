#!/bin/zsh
set -euo pipefail
export PATH='/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
SCRIPT_DIR="${0:A:h}"
TASK_RENDER_TMPDIR="$(/usr/bin/getconf DARWIN_USER_TEMP_DIR)"
export TMPDIR="${TASK_RENDER_TMPDIR%/}"
exec /usr/bin/nice -n 10 '/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/.venv/bin/python' -B "$SCRIPT_DIR/launch.py" --execute
