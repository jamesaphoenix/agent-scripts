#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == "jamesphoenix" ]] || { echo 'Studio user required' >&2; exit 1; }
TASK_SHARD_INDEX="${1:-}"
[[ "$TASK_SHARD_INDEX" == 0 || "$TASK_SHARD_INDEX" == 1 ]] || { echo 'Teaching shard 0 or 1 required' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.jud.smart-cub-teaching-review-shard-$TASK_SHARD_INDEX-20261016"
RUN_DIR="$HOME/render-tmp/smart-cub-refresh-20261016"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME="$HOME/agent-runtime/just-understanding-data/agent-scripts/agent-loops/smart-cub-teaching-review-shard-$TASK_SHARD_INDEX-20261016"
if launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | /usr/bin/grep -q 'pid ='; then
  echo 'Refusing to replace an active teaching shard' >&2
  exit 1
fi
[[ -x "$RUN_DIR/.venv/bin/python" && -f "$RUN_DIR/ledger/media-review-ledger.json" ]] || exit 1
cmp -s "$SCRIPT_DIR/teaching-review-shard.py" "$RUN_DIR/workflow/teaching-review-shard.py" || { echo 'Runtime shard source differs' >&2; exit 1; }
cmp -s "$SCRIPT_DIR/../smart-cub-marketing-review-20261016/review_admission.py" "$RUN_DIR/workflow/review_admission.py" || { echo 'Shared admission source differs' >&2; exit 1; }
mkdir -p "$RUNTIME/bin" "$HOME/Library/LaunchAgents" "$RUN_DIR/evidence"
cp "$SCRIPT_DIR/run-shard.sh" "$RUNTIME/"
cp "$SCRIPT_DIR/op-shim.py" "$RUNTIME/bin/op"
chmod 700 "$RUNTIME/bin/op"
cp "$SCRIPT_DIR/../../lib/op-service-account-env.sh" "$RUNTIME/op-service-account-env.sh"
python3 - "$PLIST" "$RUNTIME/run-shard.sh" "$RUN_DIR" "$LABEL" "$TASK_SHARD_INDEX" <<'PY'
import plistlib,sys
path,script,root,label,index=sys.argv[1:]
with open(path,'wb') as stream:
 plistlib.dump({'Label':label,'ProgramArguments':['/bin/zsh',script,index],
  'RunAtLoad':False,'KeepAlive':False,'ProcessType':'Standard','LowPriorityIO':False,
  'StandardOutPath':root+f'/evidence/teaching-review-shard-{index}-stdout.log',
  'StandardErrorPath':root+f'/evidence/teaching-review-shard-{index}-stderr.log'},stream)
PY
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed inactive $LABEL. Explicit migration proof and operator kick are required."
