#!/bin/zsh
set -euo pipefail
umask 077
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
root_dir="${1:?Runtime repository root required}"
python_bin="${2:?Pinned Python interpreter required}"
shift 2
source "$root_dir/lib/op-service-account-env.sh"
agent_scripts_export_op_service_account --required
unset ANTHROPIC_API_KEY OPENAI_API_KEY CODEX_API_KEY DATAFORSEO_LOGIN DATAFORSEO_PASSWORD
source "$root_dir/lib/op-cli.sh"
OP_CALL_TIMEOUT_SECONDS=20
trap op_cli_cleanup_isolated_home EXIT
outbound_api_key="$(run_op read 'op://api-keys/OpenAI/api_key')"
[[ -n "$outbound_api_key" ]] || { print -u2 'OpenAI credential unavailable'; exit 1; }
export OPENAI_API_KEY="$outbound_api_key"
unset outbound_api_key OP_SERVICE_ACCOUNT_TOKEN
op_cli_cleanup_isolated_home
script_dir="${0:A:h}"
exec "$python_bin" "$script_dir/focused_run.py" run "$@"
