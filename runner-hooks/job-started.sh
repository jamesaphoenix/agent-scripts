#!/usr/bin/env bash
# Preserve disk admission and bound all runner slots to two physical-host jobs.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
hook_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$hook_root/agent-loops/cache-hygiene/disk-preflight.py" --ci
exec python3 "$hook_root/lib/ci-admission.py"
