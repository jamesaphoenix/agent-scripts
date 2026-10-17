#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/opt/python@3.14/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The exact approved controller applies nice10 to its strict helper once.
exec /opt/homebrew/opt/python@3.14/bin/python3.14 -B "$SCRIPT_DIR/launch.py" --execute
