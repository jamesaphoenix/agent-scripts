#!/usr/bin/env bash
# Every product and runner shares two machine-wide job leases; slot-specific
# databases, Redis, ports and Temporal servers remain independently isolated.
set -euo pipefail
hook_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$hook_root/lib/ci-admission.py"
