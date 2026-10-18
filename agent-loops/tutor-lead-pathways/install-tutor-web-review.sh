#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || { echo 'Install on Mac Studio only.' >&2; exit 2; }
ROOT="${1:?Runtime repository root required}"
STATE="$ROOT/state/tutor-web-research-20261018-15h"
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL=com.jud.tutor-web-research-20261018-15h-review
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
/opt/homebrew/opt/python@3.14/bin/python3.14 - "$PLIST" "$SCRIPTS" "$STATE" "$LABEL" <<'PY'
import pathlib, plistlib, sys
path, scripts, state, label = sys.argv[1:]
sys.path.insert(0, scripts)
from tutor_web_review import load_authorization
load_authorization()
assert (pathlib.Path(state) / 'HANDOFF.md').is_file()
value = {'Label': label,
         'ProgramArguments': [sys.executable, scripts + '/tutor_web_review.py'],
         'RunAtLoad': True, 'StartInterval': 60, 'ProcessType': 'Standard',
         'StandardOutPath': state + '/review-stdout.log',
         'StandardErrorPath': state + '/review-stderr.log',
         'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/bin:/bin'}}
with open(path, 'wb') as stream:
    plistlib.dump(value, stream)
pathlib.Path(path).chmod(0o600)
PY
# Replace the old native reminder only. Existing profile/qualification watchers
# and their original shutdown deadline remain in force.
launchctl bootout "gui/$(id -u)/com.jud.tutor-studio-20261018-free-10h-review" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
