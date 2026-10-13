#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || { echo 'Install on Mac Studio only.' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="${1:?Extension stage required}"
ROOT_DIR="${2:?Runtime root required}"
[[ "$STAGE" =~ ^tutor-lead-pathways-[a-z0-9-]+$ ]] || exit 2
LABEL="com.jud.$STAGE-extension-watch"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
python3 - "$PLIST_PATH" "$SCRIPT_DIR" "$ROOT_DIR" "$STAGE" "$LABEL" <<'PY'
import json,pathlib,plistlib,sys
path,scripts,root,stage,label=sys.argv[1:]
sys.path.insert(0,scripts)
from campaign_extension import validate_request
from discovery import file_hash
state=pathlib.Path(root)/'state'/stage
request=state/'extension-request.json'
value=json.loads(request.read_text())
validate_request(value,pathlib.Path(value['parentCampaign']),state)
plist={'Label':label,'ProgramArguments':[sys.executable,scripts+'/campaign_extension.py',
 '--run-dir',str(state),'--request-sha256',file_hash(request),'--label',label],
 'WorkingDirectory':root,'RunAtLoad':True,'StartInterval':60,'ProcessType':'Standard',
 'StandardOutPath':str(state/'extension-watch-stdout.log'),'StandardErrorPath':str(state/'extension-watch-stderr.log'),
 'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
with open(path,'wb') as stream:plistlib.dump(plist,stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
