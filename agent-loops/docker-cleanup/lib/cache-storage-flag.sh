#!/usr/bin/env bash
# Docker/buildx renamed the storage-budget flag. Probe the installed CLI.
builder_storage_flag() {
  local help
  help="$("$@" buildx prune --help 2>&1)" || return 1
  if [[ "$help" == *"--max-used-space"* ]]; then
    printf '%s\n' '--max-used-space'
  elif [[ "$help" == *"--keep-storage"* ]]; then
    printf '%s\n' '--keep-storage'
  else
    echo 'No supported BuildKit storage-budget flag found' >&2
    return 1
  fi
}
