#!/usr/bin/env bash

# Source this file, then call:
#   agent_scripts_export_mail_oauth [profile]
#
# It exports GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN for
# the requested profile without printing secret values.

agent_scripts_mail_oauth_root() {
  local source_path="${BASH_SOURCE[0]}"
  local source_dir
  source_dir="$(cd "$(dirname "$source_path")" && pwd)"
  cd "$source_dir/.." && pwd
}

agent_scripts_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif [ -x /opt/homebrew/bin/python3 ]; then
    printf '%s\n' /opt/homebrew/bin/python3
  elif [ -x /usr/bin/python3 ]; then
    printf '%s\n' /usr/bin/python3
  else
    return 1
  fi
}

agent_scripts_mail_oauth_config_value() {
  local profile="$1"
  local path="$2"
  local py
  py="$(agent_scripts_python)" || return 1
  "$py" - "$profile" "$path" <<'PY'
import json
import sys

profile = sys.argv[1]
path = sys.argv[2]
data = json.load(open(path, "r", encoding="utf-8"))
node = data["profiles"][profile]
for part in sys.argv[3:]:
    node = node[part]
print(node)
PY
}

agent_scripts_mail_oauth_secret() {
  local profile="$1"
  local key="$2"
  local config_path="$3"
  local env_name service_name legacy_services op_ref value py

  py="$(agent_scripts_python)" || return 1
  env_name="$("$py" - "$profile" "$key" "$config_path" <<'PY'
import json
import sys
data = json.load(open(sys.argv[3], "r", encoding="utf-8"))
print(data["profiles"][sys.argv[1]].get("env", {}).get(sys.argv[2], ""))
PY
)"
  if [ -n "$env_name" ]; then
    eval "value=\"\${$env_name:-}\""
    if [ -n "$value" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  fi

  service_name="$("$py" - "$profile" "$key" "$config_path" <<'PY'
import json
import sys
data = json.load(open(sys.argv[3], "r", encoding="utf-8"))
print(data["profiles"][sys.argv[1]].get("keychainServices", {}).get(sys.argv[2], ""))
PY
)"
  if [ -n "$service_name" ]; then
    value="$(security find-generic-password -s "$service_name" -w 2>/dev/null || true)"
    if [ -n "$value" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  fi

  legacy_services="$("$py" - "$profile" "$key" "$config_path" <<'PY'
import json
import sys
data = json.load(open(sys.argv[3], "r", encoding="utf-8"))
print("\n".join(data["profiles"][sys.argv[1]].get("legacyKeychainServices", {}).get(sys.argv[2], [])))
PY
)"
  while IFS= read -r legacy_service; do
    [ -z "$legacy_service" ] && continue
    value="$(security find-generic-password -s "$legacy_service" -w 2>/dev/null || true)"
    if [ -n "$value" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  done <<< "$legacy_services"

  op_ref="$("$py" - "$profile" "$key" "$config_path" <<'PY'
import json
import sys
data = json.load(open(sys.argv[3], "r", encoding="utf-8"))
print(data["profiles"][sys.argv[1]].get("opRefs", {}).get(sys.argv[2], ""))
PY
)"
  if [ "${AGENT_SCRIPTS_MAIL_OAUTH_DISABLE_OP_FALLBACK:-0}" = "1" ]; then
    return 1
  fi

  if [ -n "$op_ref" ] && command -v op >/dev/null 2>&1; then
    value="$(op read "$op_ref" 2>/dev/null || true)"
    if [ -n "$value" ]; then
      printf '%s\n' "$value"
      return 0
    fi
  fi

  return 1
}

agent_scripts_export_mail_oauth() {
  local profile="${1:-jamesaphoenix-googlemail}"
  local root config_path client_id client_secret refresh_token

  root="$(agent_scripts_mail_oauth_root)"
  config_path="${MAIL_OAUTH_CONFIG:-$root/config/mail-oauth.json}"

  client_id="$(agent_scripts_mail_oauth_secret "$profile" clientId "$config_path")" || {
    echo "ERROR: missing Gmail OAuth client id for profile $profile" >&2
    return 1
  }
  client_secret="$(agent_scripts_mail_oauth_secret "$profile" clientSecret "$config_path")" || {
    echo "ERROR: missing Gmail OAuth client secret for profile $profile" >&2
    return 1
  }
  refresh_token="$(agent_scripts_mail_oauth_secret "$profile" refreshToken "$config_path")" || {
    echo "ERROR: missing Gmail OAuth refresh token for profile $profile" >&2
    return 1
  }

  export GMAIL_CLIENT_ID="$client_id"
  export GMAIL_CLIENT_SECRET="$client_secret"
  export GMAIL_REFRESH_TOKEN="$refresh_token"
  export MAIL_OAUTH_PROFILE="$profile"
}
