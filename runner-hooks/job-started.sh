#!/usr/bin/env bash
# Admission check only. Already running jobs and live services are untouched.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/../agent-loops/cache-hygiene/disk-preflight.py" --ci
