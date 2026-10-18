#!/bin/zsh
set -euo pipefail
umask 077
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
runtime_root="${1:?Runtime repository root required}"
python_bin="${2:?Pinned Python interpreter required}"
shift 2
source "$runtime_root/lib/op-service-account-env.sh"
agent_scripts_export_op_service_account --required
unset ANTHROPIC_API_KEY OPENAI_API_KEY CODEX_API_KEY DATAFORSEO_LOGIN DATAFORSEO_PASSWORD
script_dir="${0:A:h}"
exec "$python_bin" "$script_dir/incremental_discovery.py" launch "$@"
