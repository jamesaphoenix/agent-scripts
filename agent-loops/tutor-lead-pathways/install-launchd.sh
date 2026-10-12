#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo "Install this task on the Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_NAME="${2:-tutor-lead-pathways-20261011}"
[[ "$RUN_NAME" =~ ^tutor-lead-pathways-[a-z0-9-]+$ ]] || { echo "Choose a prepared tutor discovery run name." >&2; exit 1; }
RUN_DIR="$ROOT_DIR/state/$RUN_NAME"
MODE="${1:-run}"
[[ -f "$RUN_DIR/manifest.json" ]] || { echo "Prepare the immutable discovery plan first." >&2; exit 1; }
[[ -f "$SCRIPT_DIR/node_modules/@openai/codex-sdk/package.json" ]] || { echo "Install the pinned SDK first." >&2; exit 1; }
[[ "$MODE" == run || "$MODE" == watch ]] || { echo "Choose run or watch." >&2; exit 1; }
LABEL="$(python3 - "$SCRIPT_DIR" "$RUN_DIR/manifest.json" "$MODE" <<'PY'
import json,sys
sys.path.insert(0,sys.argv[1])
from configuration import launchd_settings
labels,interval=launchd_settings(json.load(open(sys.argv[2])))
print(labels[sys.argv[3]])
PY
)"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$RUN_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$RUN_DIR"
python3 - "$PLIST_PATH" "$SCRIPT_DIR" "$RUN_DIR" "$LABEL" "$MODE" <<'PY'
import json,pathlib,plistlib,sys
path,scripts,state,label,mode=sys.argv[1:]
sys.path.insert(0,scripts)
from configuration import launchd_settings
with open(state+"/manifest.json") as stream: labels,interval=launchd_settings(json.load(stream))
args=["/bin/zsh",scripts+"/run.zsh","--run-dir",state] if mode=="run" else ["/opt/homebrew/bin/python3",scripts+"/run.py","watch","--run-dir",state]
value={"Label":label,"ProgramArguments":args,"RunAtLoad":True,"ProcessType":"Background",
       "WorkingDirectory":str(pathlib.Path(scripts).parent.parent),
       "StandardOutPath":state+("/stdout.log" if mode=="run" else "/monitor-stdout.log"),
       "StandardErrorPath":state+("/stderr.log" if mode=="run" else "/monitor-stderr.log")}
if mode=="run":value.update(KeepAlive={"SuccessfulExit":False},ThrottleInterval=120)
else:value["StartInterval"]=interval
with open(path,"wb") as stream:plistlib.dump(value,stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for ((attempt=1;attempt<=15;attempt++)); do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
    echo "Installed $LABEL with the original finite deadline."
    exit 0
  else
    code=$?
  fi
  [[ "$code" == 5 ]] || exit "$code"
  [[ "$attempt" == 15 ]] || sleep 2
done
exit 5
