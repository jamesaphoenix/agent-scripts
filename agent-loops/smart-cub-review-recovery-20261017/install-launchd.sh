#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == 'jamesphoenix' ]] || { echo 'Studio user required' >&2; exit 1; }
FAMILY="${1:-}"
case "$FAMILY" in
  marketing) TASK='smart-cub-marketing-review-recovery-20261017' ;;
  corrected) TASK='smart-cub-corrected40-review-recovery-20261017' ;;
  *) echo 'Only the two separately released recovery families are supported' >&2; exit 1 ;;
esac
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
APPROVAL="$RUN_DIR/workflow/review-recovery-final-20261017/$FAMILY/approved-launch.json"
"$RUN_DIR/.venv/bin/python" -B "$SCRIPT_DIR/launch.py" --binding "$APPROVAL" --check
LABEL="com.jud.$TASK"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/$TASK"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | /usr/bin/grep -q 'pid ='; then
  echo 'Refusing to replace an active recovery job' >&2
  exit 1
fi
mkdir -p "$RUNTIME" "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
cp "$SCRIPT_DIR/launch.py" "$SCRIPT_DIR/recovery.py" "$SCRIPT_DIR/run.sh" "$RUNTIME/"
cp "$APPROVAL" "$RUNTIME/approved-launch.json"
cp "$SCRIPT_DIR/../../lib/op-service-account-env.sh" "$RUNTIME/op-service-account-env.sh"
python3 - "$PLIST" "$RUNTIME/run.sh" "$RUN_DIR" "$LABEL" "$FAMILY" <<'PY'
import plistlib, sys
path, script, root, label, family = sys.argv[1:]
with open(path, 'wb') as stream:
    plistlib.dump({'Label': label, 'ProgramArguments': ['/bin/zsh', script],
                  'RunAtLoad': False, 'KeepAlive': False, 'ProcessType': 'Standard',
                  'LowPriorityIO': False, 'ThrottleInterval': 60,
                  'StandardOutPath': root + '/evidence/' + family + '-review-recovery-stdout.log',
                  'StandardErrorPath': root + '/evidence/' + family + '-review-recovery-stderr.log'}, stream)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed inactive $LABEL; one explicit kick after the exact recovery release."
