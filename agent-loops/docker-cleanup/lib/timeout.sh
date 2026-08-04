#!/usr/bin/env bash

set -euo pipefail

run_with_timeout() {
  local timeout_seconds="$1"
  shift

  if [[ -z "$timeout_seconds" || ! "$timeout_seconds" =~ ^[0-9]+$ || "$timeout_seconds" -le 0 ]]; then
    echo "run_with_timeout requires a positive integer timeout in seconds." >&2
    return 2
  fi

  terminate_process_tree() {
    local root_pid="$1"
    local signal="$2"
    local child_pid

    while IFS= read -r child_pid; do
      if [[ -n "$child_pid" ]]; then
        terminate_process_tree "$child_pid" "$signal"
      fi
    done < <(pgrep -P "$root_pid" 2>/dev/null || true)

    kill "-$signal" "$root_pid" >/dev/null 2>&1 || true
  }

  "$@" &
  local command_pid="$!"

  (
    sleep "$timeout_seconds"
    if kill -0 "$command_pid" >/dev/null 2>&1; then
      echo "Command timed out after ${timeout_seconds}s: $*" >&2
      terminate_process_tree "$command_pid" TERM
      sleep 5
      terminate_process_tree "$command_pid" KILL
    fi
  ) </dev/null >&2 &
  local watchdog_pid="$!"

  local command_status=0
  if wait "$command_pid"; then
    command_status=0
  else
    command_status="$?"
  fi

  terminate_process_tree "$watchdog_pid" TERM
  wait "$watchdog_pid" 2>/dev/null || true

  return "$command_status"
}
