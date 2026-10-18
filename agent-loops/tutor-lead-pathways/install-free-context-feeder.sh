#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo "Install this task on Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${1:?Runtime repository root required}"
QUAL_STAGE="${2:-tutor-qualification-20261017-tutor-graph-16h}"
PYTHON_BIN="/opt/homebrew/opt/python@3.14/bin/python3.14"
if [[ "$QUAL_STAGE" == tutor-qualification-20261018-free-10h ]]; then
  LABEL="com.jud.tutor-free-context-20261018-free-10h"
  STATE="$ROOT_DIR/state/tutor-studio-20261018-free-10h/free-context"
else
  [[ "$QUAL_STAGE" == tutor-qualification-20261017-tutor-graph-16h ]] || exit 2
  LABEL="com.jud.tutor-free-context-20261017-tutor-graph-16h"
  STATE="$ROOT_DIR/state/tutor-studio-20261017-tutor-graph-16h/free-context"
fi
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$STATE/seed-batches" "$HOME/Library/LaunchAgents"
chmod 700 "$STATE" "$STATE/seed-batches"
"$PYTHON_BIN" - "$PLIST_PATH" "$SCRIPT_DIR" "$ROOT_DIR" "$LABEL" "$STATE" "$QUAL_STAGE" <<'PY'
import pathlib, plistlib, sys
path, scripts, runtime, label, state, qualification_stage = sys.argv[1:]
sys.path.insert(0, scripts)
from free_context_feeder import run
# Validate the exact new authorization and pinned prepared job before registration.
run(runtime, max_pages=0, qualification_stage=qualification_stage)
value = {"Label": label,
         "ProgramArguments": [sys.executable, scripts + "/free_context_feeder.py", "--runtime-root", runtime,
                              "--qualification-stage", qualification_stage],
         "RunAtLoad": True, "StartInterval": 300, "ProcessType": "Standard",
         "StandardOutPath": state + "/stdout.log", "StandardErrorPath": state + "/stderr.log",
         "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}}
with open(path, "wb") as stream: plistlib.dump(value, stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
echo "Installed bounded free owned-site context feeder."
