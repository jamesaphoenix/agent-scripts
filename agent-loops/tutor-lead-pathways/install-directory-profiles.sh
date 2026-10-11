#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo "Install this task on Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="com.jud.bucks-private-tutors-20261011"
MODE="${1:-run}"
[[ "$MODE" == run || "$MODE" == watch ]] || { echo "Choose run or watch." >&2; exit 1; }
[[ "$MODE" == run ]] || LABEL="com.jud.bucks-private-tutors-watch-20261011"
RUN_DIR="$ROOT_DIR/state/bucks-private-tutors-20261011"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$RUN_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$RUN_DIR"
/opt/homebrew/bin/python3 - "$PLIST_PATH" "$SCRIPT_DIR" "$RUN_DIR" "$LABEL" "$MODE" <<'PY'
import plistlib,sys
path,scripts,state,label,mode=sys.argv[1:]
value={"Label":label,"ProgramArguments":["/opt/homebrew/bin/python3",scripts+"/directory_profiles.py",
       "--run-dir",state,"--hours","3","--pages","5000","--target","1000",
       "--seed","https://www.findtutors.co.uk/buckinghamshire-tutors/",
       "--seed","https://tutorperch.com/find/results?age=15&level=11-plus&mode=online&send=none",
       "--seed","https://tutorperch.com/t/tracey-9ghv","--seed","https://tutorperch.com/t/jo-rutw"],
       "RunAtLoad":True,"ProcessType":"Background","KeepAlive":{"SuccessfulExit":False},"ThrottleInterval":120,
       "StandardOutPath":state+"/stdout.log","StandardErrorPath":state+"/stderr.log",
       "EnvironmentVariables":{"PATH":"/opt/homebrew/bin:/usr/bin:/bin","PYTHONUNBUFFERED":"1"}}
if mode=="watch":
 value["ProgramArguments"]=["/opt/homebrew/bin/python3",scripts+"/monitor_directory_profiles.py","--run-dir",state,
       "--owner-label","com.jud.bucks-private-tutors-20261011","--monitor-label",label]
 value.pop("KeepAlive");value.pop("ThrottleInterval");value["StartInterval"]=900
 value["StandardOutPath"]=state+"/monitor-stdout.log";value["StandardErrorPath"]=state+"/monitor-stderr.log"
with open(path,"wb") as stream:plistlib.dump(value,stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
echo "Installed bounded private tutor profile discovery. Existing checkpoints retain their deadline."
