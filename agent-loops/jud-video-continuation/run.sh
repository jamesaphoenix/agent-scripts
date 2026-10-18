#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[[ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]] || { echo "No inherited service token in the registered user domain" >&2; exit 2; }
source "$SOURCE/lib/op-service-account-env.sh"
agent_scripts_export_op_service_account --required
exec /Users/jamesphoenix/agent-runtime/just-understanding-data/video-redesign-run-20261017/runtime-venv/bin/python /Users/jamesphoenix/agent-runtime/just-understanding-data/understandingdata-web/.worktrees/video-redesign-20261017/production/brand-videos/redesign/controller.py --run /Users/jamesphoenix/agent-runtime/just-understanding-data/video-redesign-run-20261017 --phase all --allow-paid --not-before 1792337220
