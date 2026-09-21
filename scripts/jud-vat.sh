#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/lib/op-service-account-env.sh"
agent_scripts_export_op_service_account --required
exec python3 "$ROOT/lib/jud_vat.py" "$@"
