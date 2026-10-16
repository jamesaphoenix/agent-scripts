#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/op-service-account-env.sh"
agent_scripts_export_op_service_account --required
exec /usr/bin/nice -n 10 "$HOME/render-tmp/smart-cub-refresh-20261016/.venv/bin/python" -B "$SCRIPT_DIR/dispatch.py"
