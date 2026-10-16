#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TASK_RENDER_TMPDIR="$(/usr/bin/getconf DARWIN_USER_TEMP_DIR)"
export TMPDIR="${TASK_RENDER_TMPDIR%/}"
# The dispatcher waits for the pinned predecessor before checking the host lease.
# Its preflight runs the shared canonical TMPDIR verifier before renderer admission.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/nice -n 10 /Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/.venv/bin/python -B "$SCRIPT_DIR/render.py" "$@"
