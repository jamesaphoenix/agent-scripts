#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || { echo "Install on Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PAID_RUN="${1:?Choose a prepared paid run}"
PROFILE_RUN="${2:?Choose a prepared profile run}"
[[ "$PAID_RUN" =~ ^tutor-lead-pathways-[a-z0-9-]+$ && "$PROFILE_RUN" =~ ^bucks-private-tutors-[a-z0-9-]+$ ]] || exit 2
LABEL="com.jud.$PROFILE_RUN-audit"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
/opt/homebrew/bin/python3 - "$PLIST_PATH" "$SCRIPT_DIR" "$ROOT_DIR" "$PAID_RUN" "$PROFILE_RUN" "$LABEL" <<'PY'
import json,pathlib,plistlib,sys
path,scripts,root,paid,profiles,label=sys.argv[1:]
root=pathlib.Path(root)
assert (root/'state'/paid/'manifest.json').exists()
assert (root/'state'/profiles/'job.json').exists()
assert (root/'state'/profiles/'review/stage-start-baseline.json').exists()
value={'Label':label,'ProgramArguments':['/opt/homebrew/bin/python3',scripts+'/watch_expansion.py',
 '--paid-run-dir',str(root/'state'/paid),'--profile-run-dir',str(root/'state'/profiles),
 '--baseline',str(root/'state/tutor-discovery-20261011/tutors.json'),
 '--stage-baseline',str(root/'state'/profiles/'review/stage-start-baseline.json'),'--monitor-label',label],
 'WorkingDirectory':str(root),'RunAtLoad':True,'StartInterval':900,'ProcessType':'Background',
 'StandardOutPath':str(root/'state'/profiles/'audit-stdout.log'),
 'StandardErrorPath':str(root/'state'/profiles/'audit-stderr.log')}
with open(path,'wb') as stream:plistlib.dump(value,stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for ((attempt=1;attempt<=15;attempt++)); do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
    exit 0
  else
    code=$?
  fi
  [[ "$code" == 5 ]] || exit "$code"
  [[ "$attempt" == 15 ]] || sleep 2
done
exit 5
