#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo "Install on Mac Studio only." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_DIR="$ROOT_DIR/state/tracelearn-tutors-20261010-countywide"
MONITOR_DIR="$ROOT_DIR/state/tracelearn-tutor-watch-20261010"
BINARY="/Users/jamesphoenix/agent-runtime/just-understanding-data/tracelearn-outbound/bin/tutor-crawler-countywide"
LABEL="com.jud.tracelearn-tutor-watch-20261010"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$MONITOR_DIR" "$HOME/Library/LaunchAgents"
chmod 700 "$MONITOR_DIR"
[[ -f "$RUN_DIR/job.json" && -f "$RUN_DIR/discovery.json" && -x "$BINARY" ]] || { echo "Existing discovery run is required." >&2; exit 1; }
/opt/homebrew/bin/python3 "$SCRIPT_DIR/watch.py" --run-dir "$RUN_DIR" --monitor-dir "$MONITOR_DIR" --binary "$BINARY" --init
/opt/homebrew/bin/python3 - "$PLIST_PATH" "$SCRIPT_DIR/watch.py" "$RUN_DIR" "$MONITOR_DIR" "$BINARY" "$LABEL" <<'PY'
import plistlib, sys
path, script, run, state, binary, label = sys.argv[1:]
value = {"Label": label, "ProgramArguments": ["/opt/homebrew/bin/python3", script, "--run-dir", run, "--monitor-dir", state, "--binary", binary],
         "RunAtLoad": True, "StartInterval": 1800, "ProcessType": "Background",
         "StandardOutPath": state + "/stdout.log", "StandardErrorPath": state + "/stderr.log"}
with open(path, "wb") as stream:
    plistlib.dump(value, stream)
PY
chmod 600 "$PLIST_PATH"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for attempt in {1..15}; do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
    echo "Installed eight-hour tutor checks, every thirty minutes."
    exit 0
  else
    code=$?
  fi
  [[ "$code" == 5 && "$attempt" -lt 15 ]] || exit "$code"
  sleep 2
done
