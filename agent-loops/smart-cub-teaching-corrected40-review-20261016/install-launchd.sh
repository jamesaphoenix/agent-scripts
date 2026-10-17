#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo 'Studio user required' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
"$RUN_DIR/.venv/bin/python" -B "$SCRIPT_DIR/launch.py" --check
LABEL='com.jud.smart-cub-teaching-corrected40-review-20261016'
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/smart-cub-teaching-corrected40-review-20261016"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | /usr/bin/grep -q 'pid ='; then
  echo 'Refusing to replace an active corrected review job' >&2
  exit 1
fi
mkdir -p "$RUNTIME/bin" "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
cp "$SCRIPT_DIR/launch.py" "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/approved-review-batch.json" "$RUNTIME/"
cp "$SCRIPT_DIR/../../lib/op-service-account-env.sh" "$RUNTIME/op-service-account-env.sh"
cp "$SCRIPT_DIR/../smart-cub-marketing-review-20261016/op-shim.py" "$RUNTIME/bin/op"
chmod 700 "$RUNTIME/bin/op"
python3 - "$PLIST" "$RUNTIME/run.sh" "$RUN_DIR" "$LABEL" <<'PY'
import plistlib, sys
path, script, root, label = sys.argv[1:]
with open(path, 'wb') as stream:
    plistlib.dump({'Label': label, 'ProgramArguments': ['/bin/zsh', script],
        'RunAtLoad': False, 'KeepAlive': False, 'ProcessType': 'Standard',
        'LowPriorityIO': False, 'ThrottleInterval': 60,
        'StandardOutPath': root + '/evidence/teaching-corrected40-review-stdout.log',
        'StandardErrorPath': root + '/evidence/teaching-corrected40-review-stderr.log'}, stream)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed inactive $LABEL; one manual kick only after explicit paid-plan release."
