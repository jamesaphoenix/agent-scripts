#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/nice -n 10 "$HOME/render-tmp/smart-cub-refresh-20261016/.venv/bin/python" -B "$SCRIPT_DIR/render.py"
