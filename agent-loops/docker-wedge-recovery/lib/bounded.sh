#!/usr/bin/env bash
# Bounded command execution for the Docker wedge detector.
#
# WHY NOT `timeout(1)`, AND WHY NOT docker-cleanup/lib/timeout.sh
#
# The whole point of this loop is to survive a Docker daemon that accepts a
# command and then NEVER RETURNS. Anything used to bound such a command must
# itself be unkillable-proof and must never reach for a name-matched kill:
#
#   * `pkill` / `killall` targeting Docker is forbidden on this host - a repo
#     hook blocks unscoped kills, and during the 2026-08-04 incident force
#     killing Docker processes made the wedge strictly worse.
#   * docker-cleanup's run_with_timeout walks the process TREE and SIGKILLs it.
#     That is right for a janitor reclaiming images; it is wrong here, where the
#     hung process is a `docker run` attached to a daemon we are about to reboot
#     the host to fix.
#
# So this bounds work the way it was bounded by hand during the incident:
# background the command, poll `ps -p <pid>`, and on expiry send a plain
# SIGTERM to THAT ONE PID - our own direct child, captured from `$!`, never
# matched by name or pattern. If it ignores the TERM we leave it alone; a
# lingering wedged `docker run` is harmless and the reboot clears it.

set -euo pipefail

# BOUNDED_RUN_TIMED_OUT is read by the sourcing script (check.sh), which needs
# to tell "hung" from "failed fast" - that distinction is the wedge signature.
# shellcheck disable=SC2034

# Set to 1 by bounded_run when the command hit its deadline.
BOUNDED_RUN_TIMED_OUT=0

# bounded_run <timeout_seconds> <command...>
# Returns the command's exit status, or 124 if it timed out.
# Redirect at the CALL SITE to capture output, e.g.
#   bounded_run 20 docker ps >"$out" 2>&1
bounded_run() {
  local timeout_seconds="$1"
  shift

  if [[ -z "$timeout_seconds" || ! "$timeout_seconds" =~ ^[0-9]+$ || "$timeout_seconds" -le 0 ]]; then
    echo "bounded_run requires a positive integer timeout in seconds." >&2
    return 2
  fi

  BOUNDED_RUN_TIMED_OUT=0

  local pid=0
  local waited=0
  local status=0

  "$@" &
  pid="$!"

  while ps -p "$pid" >/dev/null 2>&1; do
    if (( waited >= timeout_seconds )); then
      # shellcheck disable=SC2034  # read by the sourcing script, not here
      BOUNDED_RUN_TIMED_OUT=1
      # Scoped kill: this is our own direct child pid from `$!`. Never a
      # pattern, never a process group, never Docker Desktop itself.
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done

  if wait "$pid"; then
    status=0
  else
    status="$?"
  fi

  return "$status"
}
