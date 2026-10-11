#!/bin/zsh
set -euo pipefail
umask 077
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/Users/jamesphoenix/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
# Interactive-only profile commands can return nonzero in launchd shells.
source ~/.zshrc >/dev/null 2>&1 || true
script_dir="${0:A:h}"
root_dir="${script_dir:h:h}"
source "$root_dir/lib/op-service-account-env.sh"
agent_scripts_export_op_service_account --required
unset ANTHROPIC_API_KEY OPENAI_API_KEY CODEX_API_KEY
exec python3 "$script_dir/launch.py"
