#!/bin/bash
set -euo pipefail
ROLE="${1:-}"
[[ "$ROLE" == render || "$ROLE" == review ]] || { echo 'Exact successor role required' >&2; exit 1; }
[[ "$(id -un)" == jamesphoenix ]] || { echo 'Studio user required' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK="smart-cub-marketing-successor-$ROLE-20261017"
TASK_DIR="$SCRIPT_DIR/../$TASK"
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
"$RUN_DIR/.venv/bin/python" -B "$SCRIPT_DIR/successor.py" --config "$SCRIPT_DIR/approved-successor.json" --mode "$ROLE" --check
LABEL="com.jud.$TASK"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/$TASK"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | /usr/bin/grep -q 'pid ='; then
  echo 'Refusing to replace an active successor job' >&2
  exit 1
fi
mkdir -p "$RUNTIME" "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
cp "$SCRIPT_DIR/successor.py" "$SCRIPT_DIR/approved-successor.json" "$TASK_DIR/run.sh" "$RUNTIME/"
if [[ "$ROLE" == review ]]; then
  mkdir -p "$RUNTIME/bin"
  cp "$SCRIPT_DIR/../../lib/op-service-account-env.sh" "$RUNTIME/op-service-account-env.sh"
  cp "$SCRIPT_DIR/../smart-cub-marketing-review-20261016/op-shim.py" "$RUNTIME/bin/op"
  chmod 700 "$RUNTIME/bin/op"
fi
python3 - "$PLIST" "$RUNTIME/run.sh" "$RUN_DIR" "$LABEL" "$ROLE" <<'PY'
import plistlib, sys
path, script, root, label, role = sys.argv[1:]
with open(path, 'wb') as stream:
    plistlib.dump({'Label': label, 'ProgramArguments': ['/bin/zsh', script],
        'RunAtLoad': False, 'KeepAlive': False, 'ProcessType': 'Standard',
        'LowPriorityIO': False, 'ThrottleInterval': 60,
        'StandardOutPath': root + '/evidence/marketing-successor-' + role + '-stdout.log',
        'StandardErrorPath': root + '/evidence/marketing-successor-' + role + '-stderr.log'}, stream)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed inactive $LABEL; manual kick requires exact root release."
