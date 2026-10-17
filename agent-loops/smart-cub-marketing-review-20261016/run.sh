#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SCRIPT_DIR="${0:A:h}"
if [[ "${1:-}" == "--print-script-dir" ]]; then
  printf "%s\n" "$SCRIPT_DIR"
  exit 0
fi
source "$SCRIPT_DIR/op-service-account-env.sh"
agent_scripts_export_op_service_account
if [[ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
  for task_profile in "$HOME/.zprofile" "$HOME/.zshrc"; do
    [[ -r "$task_profile" ]] || continue
    task_managed_block=0
    while IFS= read -r task_line; do
      if [[ "$task_line" == '# BEGIN OP_SERVICE_ACCOUNT_TOKEN_CLI_AUTOMATION' ]]; then
        task_managed_block=1
      elif [[ "$task_line" == '# END OP_SERVICE_ACCOUNT_TOKEN_CLI_AUTOMATION' ]]; then
        task_managed_block=0
      elif [[ "$task_managed_block" == 1 && "$task_line" == 'export OP_SERVICE_ACCOUNT_TOKEN='* ]]; then
        eval "$task_line"
      fi
    done < "$task_profile"
    [[ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]] && break
  done
fi
[[ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]] || { echo 'Managed service-account token unavailable' >&2; exit 1; }
export PATH="$SCRIPT_DIR/bin:$PATH"
exec /usr/bin/nice -n 10 "$HOME/render-tmp/smart-cub-refresh-20261016/.venv/bin/python" -B "$SCRIPT_DIR/dispatch.py" --revision-config "$HOME/render-tmp/smart-cub-refresh-20261016/workflow/marketing-revision-dispatch-config.json" "$@"
