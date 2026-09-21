#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_SSH="${AGENT_SCRIPTS_DEPLOY_TARGET_SSH:-jamesphoenix@jamess-mac-studio}"
RUNTIME_TARGET="${AGENT_SCRIPTS_RUNTIME_TARGET:-/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/}"
DESKTOP_TARGET="${AGENT_SCRIPTS_DESKTOP_TARGET:-/Users/jamesphoenix/Desktop/projects/just-understanding-data/agent-scripts/}"
MODE="dry-run"

usage() {
  cat <<'HELP'
Usage:
  scripts/deploy-to-mac-studio.sh --dry-run
  scripts/deploy-to-mac-studio.sh --live

Deploy the MacBook-authored agent-scripts tree to the Mac Studio runtime and
visible Desktop copies. This is one-way deployment, not bidirectional sync.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --live)
      MODE="live"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

rsync_args=(
  -az
  --human-readable
  --stats
  --exclude .git/
  --exclude state/
  --exclude .tx/
  --exclude __pycache__/
  --exclude '*.pyc'
)

if [ "$MODE" = "dry-run" ]; then
  rsync_args+=(--dry-run)
fi

echo "agent-scripts deploy mode=$MODE"
echo "source=$ROOT/"
echo "runtime_target=$TARGET_SSH:$RUNTIME_TARGET"
echo "desktop_target=$TARGET_SSH:$DESKTOP_TARGET"
echo "delete_remote_files=false"

ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET_SSH" \
  "mkdir -p '$RUNTIME_TARGET' '$DESKTOP_TARGET' && test -d '$RUNTIME_TARGET' && test -d '$DESKTOP_TARGET'"

echo "deploying runtime copy"
rsync "${rsync_args[@]}" "$ROOT/" "$TARGET_SSH:$RUNTIME_TARGET"

echo "deploying desktop visibility copy"
rsync "${rsync_args[@]}" "$ROOT/" "$TARGET_SSH:$DESKTOP_TARGET"

echo "agent-scripts deploy complete"
