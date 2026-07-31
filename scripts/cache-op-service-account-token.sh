#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SERVICE="${AGENT_SCRIPTS_OP_SERVICE_ACCOUNT_KEYCHAIN_SERVICE:-op_service_account_token_cli_automation}"
ENV_NAME="OP_SERVICE_ACCOUNT_TOKEN"
OP_REF=""
MODE="ensure"

usage() {
  cat <<'HELP'
Usage:
  scripts/cache-op-service-account-token.sh [--ensure]
  scripts/cache-op-service-account-token.sh --check
  scripts/cache-op-service-account-token.sh --from-env ENV_NAME
  scripts/cache-op-service-account-token.sh --op-ref op://vault/item/field

Caches the 1Password service account token into the macOS login keychain under
the shared service name used by launchd wrappers. The token is never printed.

Run this from an unlocked local Terminal session on the target Mac.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ensure)
      MODE="ensure"
      shift
      ;;
    --check)
      MODE="check"
      shift
      ;;
    --from-env)
      ENV_NAME="$2"
      shift 2
      ;;
    --op-ref)
      OP_REF="$2"
      shift 2
      ;;
    --service)
      SERVICE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

existing="$(security find-generic-password -s "$SERVICE" -w 2>/dev/null || true)"
if [ -n "$existing" ]; then
  echo "OP service account token already cached in keychain service $SERVICE."
  exit 0
fi

if [ "$MODE" = "check" ]; then
  echo "ERROR: OP service account token is not cached in keychain service $SERVICE." >&2
  exit 1
fi

token=""
eval "token=\"\${$ENV_NAME:-}\""

if [ -z "$token" ] && [ -n "$OP_REF" ]; then
  if ! command -v op >/dev/null 2>&1; then
    echo "ERROR: op CLI is required to read $OP_REF" >&2
    exit 1
  fi
  token="$(op read "$OP_REF")"
fi

if [ -z "$token" ]; then
  if [ ! -t 0 ]; then
    echo "ERROR: $ENV_NAME is empty and no TTY is available for a hidden prompt." >&2
    exit 1
  fi
  printf 'Paste OP service account token for this Mac user: ' >&2
  IFS= read -r -s token
  printf '\n' >&2
fi

if [ -z "$token" ]; then
  echo "ERROR: empty OP service account token." >&2
  exit 1
fi

if security add-generic-password -U -s "$SERVICE" -a "$USER" -w "$token" >/dev/null 2>&1; then
  echo "Cached OP service account token in keychain service $SERVICE."
else
  echo "ERROR: could not write keychain service $SERVICE. Run from an unlocked local Terminal on the target Mac." >&2
  exit 1
fi
