#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || exit 1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK='smart-cub-marketing-render-recovery-20261017'
RUN_DIR='/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016'
APPROVAL="$RUN_DIR/workflow/marketing-render-recovery-20261017/approved-launch.json"
"$RUN_DIR/.venv/bin/python" -B "$SCRIPT_DIR/launch.py" --binding "$APPROVAL" --check
LABEL="com.jud.$TASK"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/$TASK"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | /usr/bin/grep -q 'pid ='; then echo 'Refusing active task replacement' >&2; exit 1; fi
mkdir -p "$RUNTIME" "$HOME/Library/LaunchAgents"
cp "$SCRIPT_DIR/launch.py" "$SCRIPT_DIR/render.py" "$SCRIPT_DIR/reconcile.py" "$SCRIPT_DIR/run.sh" "$RUNTIME/"
cp "$APPROVAL" "$RUNTIME/approved-launch.json"
python3 - "$PLIST" "$LABEL" "$RUNTIME/run.sh" "$RUN_DIR" <<'PY'
import plistlib,sys
p,label,script,root=sys.argv[1:]
with open(p,'wb') as f:plistlib.dump({'Label':label,'ProgramArguments':['/bin/zsh',script],'RunAtLoad':False,'KeepAlive':False,'ProcessType':'Standard','LowPriorityIO':False,'ThrottleInterval':60,'StandardOutPath':root+'/evidence/marketing-render-recovery-stdout.log','StandardErrorPath':root+'/evidence/marketing-render-recovery-stderr.log'},f)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed inactive $LABEL"
