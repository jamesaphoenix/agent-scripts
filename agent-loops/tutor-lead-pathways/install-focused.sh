#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || { echo "Install on Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$(command -v python3)"
MODE="${1:?Choose run or watch}"
STAGE="${2:?Choose a prepared stage}"
[[ "$MODE" == run || "$MODE" == watch ]] || exit 2
[[ "$STAGE" =~ ^tutor-focused-[a-z0-9-]+$ ]] || exit 2
RUN_DIR="$ROOT_DIR/state/$STAGE"
[[ -f "$RUN_DIR/job.json" && -x "$PYTHON_BIN" ]] || exit 2
OWNER="com.jud.$STAGE"
MONITOR="$OWNER-watch"
LABEL="$OWNER"
[[ "$MODE" == run ]] || LABEL="$MONITOR"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents"
"$PYTHON_BIN" - "$PLIST_PATH" "$RUN_DIR" "$ROOT_DIR" "$LABEL" "$MODE" "$OWNER" "$MONITOR" <<'PY'
import json,pathlib,plistlib,sys
path,state,root,label,mode,owner,monitor=sys.argv[1:]
job=json.loads((pathlib.Path(state)/'job.json').read_text())
scripts=pathlib.Path(job['sourceDir'])
assert 0 < job['classificationCapNano'] <= 3_000_000_000
assert job['priorExposureCents'] * 10_000_000 + job['classificationCapNano'] <= 100_000_000_000
assert all((scripts/f).is_file() for f in ['focused_run.py','focused.zsh','focused_frontier.py','decisions_classifier.py'])
value={'Label':label,'ProgramArguments':['/bin/zsh',str(scripts/'focused.zsh'),root,sys.executable,'--run-dir',state],
       'WorkingDirectory':root,'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':120,
       'ProcessType':'Background','StandardOutPath':state+'/stdout.log','StandardErrorPath':state+'/stderr.log',
       'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
if mode=='watch':
 value['ProgramArguments']=[sys.executable,str(scripts/'focused_run.py'),'watch','--run-dir',state,
                           '--owner-label',owner,'--monitor-label',monitor]
 value.pop('KeepAlive');value.pop('ThrottleInterval');value['StartInterval']=900
 value['StandardOutPath']=state+'/watch-stdout.log';value['StandardErrorPath']=state+'/watch-stderr.log'
with open(path,'wb') as stream: plistlib.dump(value,stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
echo "Installed finite tutor relevance crawl: $LABEL"
