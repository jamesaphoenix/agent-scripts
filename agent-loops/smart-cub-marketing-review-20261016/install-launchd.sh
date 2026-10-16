#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo 'Studio user required' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL='com.jud.smart-cub-marketing-review-20261016'
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/smart-cub-marketing-review-20261016"
[[ -x "$RUN_DIR/.venv/bin/python" ]] || { echo 'Missing existing task virtualenv' >&2; exit 1; }
[[ -f "$RUN_DIR/ledger/media-review-ledger.json" ]] || { echo 'Missing authoritative ledger' >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
mkdir -p "$RUNTIME"
cp "$SCRIPT_DIR/run.sh" "$SCRIPT_DIR/dispatch.py" "$RUNTIME/"
cp "$SCRIPT_DIR/../../lib/op-service-account-env.sh" "$RUNTIME/op-service-account-env.sh"
python3 - "$PLIST" "$RUNTIME/run.sh" "$RUN_DIR" "$LABEL" <<'PY'
import plistlib,sys
path,script,root,label=sys.argv[1:]
with open(path,'wb') as stream:
 plistlib.dump({'Label':label,'ProgramArguments':['/bin/zsh',script],
  'RunAtLoad':True,'KeepAlive':False,'ProcessType':'Background','LowPriorityIO':True,
  'StandardOutPath':root+'/evidence/marketing-review-dispatch-stdout.log',
  'StandardErrorPath':root+'/evidence/marketing-review-dispatch-stderr.log'},stream)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed one-shot $LABEL; no KeepAlive and no scheduled repeat."
