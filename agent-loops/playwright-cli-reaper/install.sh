#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SOURCE="$SCRIPT_DIR/reap.sh"
INSTALL_DIR="${PLAYWRIGHT_REAPER_INSTALL_DIR:-$HOME/.local/bin}"
TARGET="$INSTALL_DIR/playwright-reap"
FORCE=0

if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
elif [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: agent-loops/playwright-cli-reaper/install.sh [--force]"
  exit 0
elif [[ "$#" -gt 0 ]]; then
  echo "Unknown argument: $1" >&2
  exit 2
fi

if [[ ! -x "$SOURCE" ]]; then
  echo "Reaper is not executable: $SOURCE" >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  current_target="$(readlink "$TARGET" 2>/dev/null || true)"
  if [[ "$current_target" == "$SOURCE" ]]; then
    echo "Already installed: $TARGET -> $SOURCE"
    exit 0
  fi
  if (( FORCE == 0 )); then
    echo "Refusing to replace existing path: $TARGET" >&2
    echo "Re-run with --force after inspecting it." >&2
    exit 1
  fi
fi

ln -sfn "$SOURCE" "$TARGET"
echo "Installed: $TARGET -> $SOURCE"
