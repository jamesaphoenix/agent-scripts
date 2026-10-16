#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo 'Studio user required' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL='com.jud.smart-cub-marketing-render-20261016'
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/smart-cub-marketing-render-20261016"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | /usr/bin/grep -q 'pid ='; then
  echo 'Refusing to replace an active marketing service' >&2
  exit 1
fi
mkdir -p "$RUNTIME" "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
cp "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/render.py" "$RUNTIME/"
python3 - "$PLIST" "$RUNTIME/run.sh" "$RUN_DIR" "$LABEL" <<'PY'
import plistlib,sys
path,script,root,label=sys.argv[1:]
with open(path,'wb') as stream:
 plistlib.dump({'Label':label,'ProgramArguments':['/bin/bash',script],
  'RunAtLoad':False,'KeepAlive':False,'ProcessType':'Standard','LowPriorityIO':False,
  'StandardOutPath':root+'/evidence/marketing-render-dispatch-stdout.log',
  'StandardErrorPath':root+'/evidence/marketing-render-dispatch-stderr.log'},stream)
PY
cp "$SCRIPT_DIR/../smart-cub-marketing-review-20261016/revision_config.py" "$RUNTIME/revision_config.py"
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed inactive $LABEL. Kick only after verified prior controller cleanup and lease release."
