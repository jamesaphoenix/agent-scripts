#!/usr/bin/env bash

# Source this file, then call:
#   agent_scripts_export_op_service_account [--required]
#
# It exports OP_SERVICE_ACCOUNT_TOKEN from the macOS login keychain without
# printing the token. Runtime jobs can then use `op read` non-interactively.

agent_scripts_op_service_name() {
  printf '%s\n' "${AGENT_SCRIPTS_OP_SERVICE_ACCOUNT_KEYCHAIN_SERVICE:-op_service_account_token_cli_automation}"
}

agent_scripts_export_op_service_account() {
  local mode="${1:-}"
  local service token

  if [ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]; then
    return 0
  fi

  service="$(agent_scripts_op_service_name)"
  token="$(security find-generic-password -s "$service" -w 2>/dev/null || true)"
  if [ -n "$token" ]; then
    export OP_SERVICE_ACCOUNT_TOKEN="$token"
    return 0
  fi

  if [ "$mode" = "--required" ] || [ "$mode" = "required" ]; then
    echo "ERROR: OP service account token is not cached in keychain service $service" >&2
    echo "Run agent-scripts/scripts/cache-op-service-account-token.sh from an unlocked local Terminal on this Mac." >&2
    return 1
  fi

  return 0
}
