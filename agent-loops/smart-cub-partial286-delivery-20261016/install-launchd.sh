#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo 'Studio user required' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
/opt/homebrew/opt/python@3.14/bin/python3.14 -B "$SCRIPT_DIR/launch.py" --check
LABEL='com.jud.smart-cub-partial286-delivery-20261016'
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/smart-cub-partial286-delivery-20261016"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | /usr/bin/grep -q 'pid ='; then
  echo 'Refusing to replace active delivery controller' >&2
  exit 1
fi
mkdir -p "$RUNTIME" "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
cp "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/launch.py" "$RUNTIME/"
/opt/homebrew/opt/python@3.14/bin/python3.14 - "$PLIST" "$RUNTIME/run.sh" "$RUN_DIR" "$LABEL" <<'PY'
import plistlib,sys
path,script,root,label=sys.argv[1:]
with open(path,'wb')as stream:
 plistlib.dump({'Label':label,'ProgramArguments':['/bin/bash',script], 'RunAtLoad':False,'KeepAlive':False,'ProcessType':'Standard','LowPriorityIO':False,'ThrottleInterval':60,'StandardOutPath':root+'/evidence/partial286-delivery-execute-stdout.log','StandardErrorPath':root+'/evidence/partial286-delivery-execute-stderr.log'},stream)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed inactive $LABEL; one root-authorised manual kick."
