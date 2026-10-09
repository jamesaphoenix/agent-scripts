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
# BEGIN bootstrap_launch_agent
bootstrap_launch_agent() {
  local domain="$1" label="$2" plist="$3"
  local attempt bootstrap_code
  launchctl bootout "$domain/$label" 2>/dev/null || true
  # launchd can still be unregistering the old service when bootout returns.
  # Only its transient I/O exit is retried, for at most 28 seconds. This does
  # not replay provider calls or change the saved job deadline and budget.
  for ((attempt = 1; attempt <= 15; attempt++)); do
    if launchctl bootstrap "$domain" "$plist"; then
      return 0
    else
      bootstrap_code=$?
    fi
    [[ "$bootstrap_code" == 5 ]] || return "$bootstrap_code"
    if ((attempt < 15)); then sleep 2; fi
  done
  return 5
}
# END bootstrap_launch_agent
bootstrap_launch_agent "gui/$(id -u)" "$LABEL" "$PLIST_PATH"
echo "Installed $LABEL. The stored deadline and budget survive restarts."
