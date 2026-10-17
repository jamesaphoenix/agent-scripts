#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TASK_RENDER_TMPDIR="$(/usr/bin/getconf DARWIN_USER_TEMP_DIR)"
export TMPDIR="${TASK_RENDER_TMPDIR%/}"
/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/.venv/bin/python -B /Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/workflow/render_tmpdir.py
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/nice -n 10 /Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/.venv/bin/python -B "$SCRIPT_DIR/render.py" "$@"
