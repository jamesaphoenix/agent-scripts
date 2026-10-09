#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo "Install this job on the Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_DIR="$ROOT_DIR/state/tracelearn-tutors-20261009"
LABEL="com.jud.tracelearn-tutor-discovery-20261009"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
[[ -x /Users/jamesphoenix/agent-runtime/just-understanding-data/tracelearn-outbound/bin/tutor-crawler ]] || { echo "Deploy the reviewed crawler binary first." >&2; exit 1; }
mkdir -p "$RUN_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$RUN_DIR"
python3 - "$PLIST_PATH" "$SCRIPT_DIR/run.zsh" "$RUN_DIR" "$LABEL" <<'PY'
import plistlib, sys
path, script, state, label = sys.argv[1:]
value = {"Label": label, "ProgramArguments": ["/bin/zsh", script], "RunAtLoad": True,
         "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 120,
         "StandardOutPath": state + "/stdout.log", "StandardErrorPath": state + "/stderr.log",
         "ProcessType": "Background"}
with open(path, "wb") as stream:
    plistlib.dump(value, stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
echo "Installed $LABEL. The stored deadline and budget survive restarts."
