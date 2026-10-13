#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo "Install this task on Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${3:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PYTHON_BIN="$(command -v python3)"
[[ "$PYTHON_BIN" == /* && -x "$PYTHON_BIN" ]] || { echo "An installed Python 3 interpreter is required." >&2; exit 1; }
MODE="${1:-run}"
STAGE="${2:-bucks-private-tutors-20261011}"
[[ "$MODE" == run || "$MODE" == watch ]] || { echo "Choose run or watch." >&2; exit 1; }
[[ "$STAGE" =~ ^[a-z0-9][a-z0-9-]{0,100}$ ]] || { echo "Choose a plain registered stage name." >&2; exit 1; }
OWNER="com.jud.$STAGE"
MONITOR="com.jud.$STAGE-watch"
[[ "$STAGE" != bucks-private-tutors-20261011 ]] || MONITOR="com.jud.bucks-private-tutors-watch-20261011"
LABEL="$OWNER"
[[ "$MODE" == run ]] || LABEL="$MONITOR"
RUN_DIR="$ROOT_DIR/state/$STAGE"
[[ "$STAGE" == bucks-private-tutors-20261011 || -f "$RUN_DIR/job.json" ]] || { echo "Prepare the bounded follow-on stage first." >&2; exit 1; }
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$RUN_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$RUN_DIR"
"$PYTHON_BIN" - "$PLIST_PATH" "$SCRIPT_DIR" "$RUN_DIR" "$LABEL" "$MODE" "$OWNER" "$MONITOR" <<'PY'
import datetime,json,pathlib,plistlib,sys
path,scripts,state,label,mode,owner,monitor=sys.argv[1:]
job_path=pathlib.Path(state)/"job.json"
hours,pages,target=3,5000,1000
if job_path.exists():
 job=json.loads(job_path.read_text())
 hours=(job["deadlineUnix"]-datetime.datetime.fromisoformat(job["startedAt"]).timestamp())/3600
 pages,target=job["pageLimit"],job["target"]
 # Earlier prepared jobs used two clock reads, differing by microseconds.
 assert 0<hours<=12+1/3600000 and 1<=pages<=20000 and 1<=target<=5000
 hours=min(hours,12)
value={"Label":label,"ProgramArguments":[sys.executable,scripts+"/directory_profiles.py",
       "--run-dir",state,"--hours",str(hours),"--pages",str(pages),"--target",str(target),
       "--seed","https://www.findtutors.co.uk/buckinghamshire-tutors/",
       "--seed","https://tutorperch.com/find/results?age=15&level=11-plus&mode=online&send=none",
       "--seed","https://tutorperch.com/t/tracey-9ghv","--seed","https://tutorperch.com/t/jo-rutw"],
       "RunAtLoad":True,"ProcessType":"Standard","KeepAlive":{"SuccessfulExit":False},"ThrottleInterval":120,
       "StandardOutPath":state+"/stdout.log","StandardErrorPath":state+"/stderr.log",
       "EnvironmentVariables":{"PATH":"/opt/homebrew/bin:/usr/bin:/bin","PYTHONUNBUFFERED":"1"}}
if mode=="watch":
 value["ProgramArguments"]=[sys.executable,scripts+"/monitor_directory_profiles.py","--run-dir",state,
       "--owner-label",owner,"--monitor-label",monitor]
 value.pop("KeepAlive");value.pop("ThrottleInterval");value["StartInterval"]=900
 value["StandardOutPath"]=state+"/monitor-stdout.log";value["StandardErrorPath"]=state+"/monitor-stderr.log"
with open(path,"wb") as stream:plistlib.dump(value,stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
echo "Installed bounded private tutor profile discovery. Existing checkpoints retain their deadline."
