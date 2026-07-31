#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${MAIL_OAUTH_CONFIG:-$ROOT/config/mail-oauth.json}"
PROFILE="${MAIL_OAUTH_PROFILE:-jamesaphoenix-googlemail}"

# shellcheck source=/dev/null
source "$ROOT/lib/op-service-account-env.sh"
agent_scripts_export_op_service_account

usage() {
  cat <<'HELP'
Usage:
  scripts/cache-mail-oauth.sh [--profile name]

Resolves the Gmail OAuth op:// refs for a profile and caches the values in the
macOS login keychain under central agent_mail_* service names. Secret values are
not printed.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      PROFILE="$2"
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

if ! command -v op >/dev/null 2>&1; then
  echo "ERROR: op CLI is required" >&2
  exit 1
fi

PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ] && [ -x /opt/homebrew/bin/python3 ]; then
  PYTHON_BIN="/opt/homebrew/bin/python3"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: python3 is required" >&2
  exit 1
fi

profile_json="$("$PYTHON_BIN" - "$CONFIG" "$PROFILE" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
profile = data["profiles"][sys.argv[2]]
for key, secret_key in [
    ("clientId", "client_id"),
    ("clientSecret", "client_secret"),
    ("refreshToken", "refresh_token"),
]:
    print("\t".join([key, profile["opRefs"][key], profile["keychainServices"][key]]))
PY
)"

cache_failed=0
while IFS=$'\t' read -r key op_ref service_name; do
  [ -z "$key" ] && continue
  value="$(op read "$op_ref")"
  if [ -z "$value" ]; then
    echo "ERROR: empty value resolving $key from $op_ref" >&2
    exit 1
  fi
  if security add-generic-password -U -s "$service_name" -a "$USER" -w "$value" >/dev/null 2>&1; then
    echo "cached $service_name"
  else
    echo "ERROR: could not cache $service_name in the login keychain; run this from an unlocked local Terminal on the target Mac" >&2
    cache_failed=1
  fi
done <<< "$profile_json"

if [ "$cache_failed" -ne 0 ]; then
  exit 1
fi

echo "Cached Gmail OAuth profile $PROFILE in the login keychain."
