#!/usr/bin/env bash
# Local, on-demand MCP setup. Does not install add-ons or change the open Blender scene.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
launcher="$SCRIPT_DIR/blender-lab-mcp"
link="$HOME/.local/bin/blender-lab-mcp"
mkdir -p "$HOME/.local/bin"
if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo 'blender-lab-mcp: an existing regular launcher file must be reviewed before replacement' >&2
    exit 1
fi
ln -sfn "$launcher" "$link"
"$launcher" configure --client both
"$launcher" doctor --client both
