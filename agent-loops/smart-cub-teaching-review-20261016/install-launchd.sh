#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo 'Studio user required' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL='com.jud.smart-cub-teaching-review-20261016'
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/smart-cub-teaching-review-20261016"
[[ -x "$RUN_DIR/.venv/bin/python" ]] || { echo 'Missing existing task virtualenv' >&2; exit 1; }
[[ -f "$RUN_DIR/ledger/media-review-ledger.json" ]] || { echo 'Missing authoritative ledger' >&2; exit 1; }
[[ "$(shasum -a 256 "$RUN_DIR/workflow/teaching-review-dispatch.py" | cut -d ' ' -f1)" == '7b419925ae0b1b2aa71fc3cfa11291220b1de2a669c745b4e016455e9c97a762' ]] || { echo 'Teaching wrapper changed; reconcile before install' >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
mkdir -p "$RUNTIME/bin"
cp "$SCRIPT_DIR/run.sh" "$RUNTIME/"
cp "$SCRIPT_DIR/op-shim.py" "$RUNTIME/bin/op"
chmod 700 "$RUNTIME/bin/op"
cp "$SCRIPT_DIR/../../lib/op-service-account-env.sh" "$RUNTIME/op-service-account-env.sh"
python3 - "$PLIST" "$RUNTIME/run.sh" "$RUN_DIR" "$LABEL" <<'PY'
import plistlib,sys
path,script,root,label=sys.argv[1:]
with open(path,'wb') as stream:
 plistlib.dump({'Label':label,'ProgramArguments':['/bin/zsh',script],
  'RunAtLoad':False,'KeepAlive':False,'ProcessType':'Standard','LowPriorityIO':False,
  'StandardOutPath':root+'/evidence/teaching-review-dispatch-stdout.log',
  'StandardErrorPath':root+'/evidence/teaching-review-dispatch-stderr.log'},stream)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed one-shot $LABEL; no KeepAlive and no scheduled repeat."
