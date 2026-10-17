#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || exit 2
ROOT="${1:?Runtime repository root required}"
STATE="$ROOT/state/tutor-studio-20261017-free-12h"
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/studio_review_check.py"
LABEL=com.jud.tutor-studio-20261017-free-12h-review
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
/opt/homebrew/opt/python@3.14/bin/python3.14 - "$PLIST" "$SCRIPT" "$STATE" "$LABEL" <<'PY'
import pathlib,plistlib,sys
path,script,state,label=sys.argv[1:]
value={'Label':label,'ProgramArguments':['/opt/homebrew/opt/python@3.14/bin/python3.14',script],
       'RunAtLoad':True,'StartInterval':1800,'ProcessType':'Standard',
       'StandardOutPath':state+'/review-stdout.log','StandardErrorPath':state+'/review-stderr.log',
       'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/bin:/bin'}}
pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
with open(path,'wb') as output:plistlib.dump(value,output)
pathlib.Path(path).chmod(0o600)
PY
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
