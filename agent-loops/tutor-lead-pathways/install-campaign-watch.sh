#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || { echo 'Install on Mac Studio only.' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${2:?Runtime root required}"
STAGE="${1:?Prepared campaign required}"
[[ "$STAGE" =~ ^tutor-lead-pathways-[a-z0-9-]+$ ]] || exit 2
LABEL="com.jud.$STAGE-campaign-watch"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
python3 - "$PLIST_PATH" "$SCRIPT_DIR" "$ROOT_DIR" "$STAGE" "$LABEL" <<'PY'
import json,pathlib,plistlib,sys
path,scripts,root,stage,label=sys.argv[1:]
state=pathlib.Path(root)/'state'/stage
assert json.loads((state/'manifest.json').read_text())['hours']==12
value={'Label':label,'ProgramArguments':[sys.executable,scripts+'/campaign_watch.py','--run-dir',str(state),'--label',label],
 'WorkingDirectory':root,'RunAtLoad':True,'StartInterval':900,'ProcessType':'Background',
 'StandardOutPath':str(state/'campaign-watch-stdout.log'),'StandardErrorPath':str(state/'campaign-watch-stderr.log'),
 'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
with open(path,'wb') as stream:plistlib.dump(value,stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
