#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
INSTALL_DIR=${GTMCTL_INSTALL_DIR:-"${HOME:?HOME is required}/.local/bin"}
TARGET="$INSTALL_DIR/gtmctl"

mkdir -p "$INSTALL_DIR"
ln -sfn "$SCRIPT_DIR/gtmctl" "$TARGET"
chmod +x "$SCRIPT_DIR/gtmctl" "$SCRIPT_DIR/gtmctl.py"

printf '%s\n' "Installed gtmctl at $TARGET"
