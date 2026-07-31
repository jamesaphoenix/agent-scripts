#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REGISTRY="$SCRIPT_DIR/registry.json"

if [ ! -f "$REGISTRY" ]; then
  echo "No loop registry found at $REGISTRY" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for loop status" >&2
  exit 1
fi

echo "Registered engineering loops"
echo

jq -r '.loops[] | [.id, .name, .script, (.launchdLabel // ""), (.statusCommand // "")] | @tsv' "$REGISTRY" |
  while IFS=$'\t' read -r id name script launchd_label status_command; do
    echo "[$id] $name"
    echo "  script: $script"

    if [ -n "$launchd_label" ]; then
      if launchctl list 2>/dev/null | grep -F "$launchd_label" >/dev/null 2>&1; then
        launch_line="$(launchctl list 2>/dev/null | grep -F "$launchd_label" | head -1)"
        echo "  launchd: loaded ($launch_line)"
      else
        echo "  launchd: not loaded ($launchd_label)"
      fi
    fi

    if [ -n "$status_command" ]; then
      status_path="${ROOT_DIR}/${status_command%% *}"
      if [ -x "$status_path" ]; then
        echo "  status:"
        (cd "$ROOT_DIR" && bash -lc "$status_command") | sed 's/^/    /'
      else
        echo "  status: command not executable ($status_command)"
      fi
    fi

    echo
  done
