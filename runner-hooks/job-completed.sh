#!/usr/bin/env bash
#
# GitHub Actions ACTIONS_RUNNER_HOOK_JOB_COMPLETED hook for the Mac Studio runners.
#
# Reaps processes a finished job left behind. Run with --dry-run to print what it WOULD
# kill without killing anything.
#
# ── Why this exists ─────────────────────────────────────────────────────────────────────
#
# The runner cannot clean up after itself on macOS, for two independent reasons, both
# measured on this host:
#
#   1. At job timeout the runner logs "Kill entire process tree" but signals only the
#      step's DIRECT child. A wedged `gcloud auth configure-docker` wrapper survived that,
#      reparented to ppid=1, and lived for 25 HOURS.
#   2. Its own orphan sweep identifies strays by reading RUNNER_TRACKING_ID out of `ps e`.
#      macOS blanks the environment of SIP-protected binaries, so that is invisible for
#      /bin/sh and /bin/bash (verified: 0 matches; a homebrew node process shows its env
#      fine). Every `run:` step is /bin/bash and gcloud's launcher is #!/bin/sh, so the
#      sweep is structurally blind to exactly the processes that strand.
#
# The four runner slots are SHARED between products, so one stranded process starves the
# other product's deploys. That is not hypothetical - it delayed live deploys by 10 minutes
# and the 25h orphan had been silently holding capacity for a day.
#
# ── Safety: this box runs LIVE SERVICES ─────────────────────────────────────────────────
#
# Production traffic for both products is served from this machine: Docker Desktop runs the
# prod and staging containers, and cloudflared runs the tunnels behind api.tracelearn.app.
# A naive `pkill -f docker` or `pkill -f cloudflared` here would take PRODUCTION DOWN.
#
# So a process is killed only when ALL of these hold:
#
#   1. Its cwd is under THIS SLOT's _work root. Live services cannot satisfy this: Docker
#      Desktop's cwd is ~/Library/Containers/com.docker.docker/Data and cloudflared's is
#      elsewhere - never inside a runner workspace. This is the load-bearing check, and it
#      is an allow-list (must match) rather than a deny-list (must not match).
#   2. It is older than MIN_AGE_SECONDS, so the current job's own teardown is never raced.
#   3. It does not match the deny-list below. This is belt-and-braces only; check 1 already
#      excludes every live service. Names are matched, never used as the sole criterion.
#
# ── NEVER kill a process group here ─────────────────────────────────────────────────────
#
# The op wrapper bounds `op` by SIGKILLing its process GROUP, and that is correct THERE,
# because it forks and calls setpgrp(0,0) first, so the group contains only that child and
# its descendants.
#
# Applying the same idea here is catastrophic and was measured to be: CI processes do NOT
# get their own group, they INHERIT the runner's. Every candidate on this host reported
# pgid=803, which was the process group of the runner-3 service itself. A single
# `kill -KILL -803` therefore killed Runner.Listener for that slot and the job running on
# it. The runner had to be restarted by hand.
#
# So strays are killed by walking the ppid tree from the specific pid instead, and the
# runner's own processes are refused outright.

set -uo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# macOS `ps` has NO `etimes` (elapsed seconds) field - only `etime`, formatted as
# [[DD-]HH:]MM:SS. Asking for etimes does not error cleanly either: ps dumps its entire
# keyword list, which then lands in an arithmetic context and aborts the hook. Parse the
# formatted value instead.
etime_to_seconds() {
  local raw="$1" days=0 rest secs=0
  [[ -z "$raw" ]] && { printf '0'; return; }
  if [[ "$raw" == *-* ]]; then
    days="${raw%%-*}"
    rest="${raw#*-}"
  else
    rest="$raw"
  fi
  local IFS=':'
  read -r -a parts <<< "$rest"
  case "${#parts[@]}" in
    3) secs=$(( 10#${parts[0]} * 3600 + 10#${parts[1]} * 60 + 10#${parts[2]} )) ;;
    2) secs=$(( 10#${parts[0]} * 60 + 10#${parts[1]} )) ;;
    1) secs=$(( 10#${parts[0]} )) ;;
  esac
  printf '%s' $(( 10#$days * 86400 + secs ))
}

MIN_AGE_SECONDS="${RUNNER_REAP_MIN_AGE_SECONDS:-120}"

# Kill a pid and its descendants, deepest first, by PID only. Never signals a process
# group - see the note above about pgid inheritance killing the runner itself. Refuses any
# pid belonging to the runner, whatever the caller passed in.
kill_tree() {
  local root="$1" child
  [[ -z "$root" || "$root" == "1" || "$root" == "$$" ]] && return 0
  local rcmd
  rcmd="$(ps -o command= -p "$root" 2>/dev/null)"
  if [[ "$rcmd" =~ Runner\.Listener|Runner\.Worker ]]; then
    echo "runner-reap: refusing to signal runner process pid=$root" >&2
    return 0
  fi
  for child in $(pgrep -P "$root" 2>/dev/null); do
    kill_tree "$child"
  done
  kill -TERM "$root" 2>/dev/null
  sleep 1
  kill -KILL "$root" 2>/dev/null
}

# Anything matching this is never signalled, whatever else is true of it.
# com.docker/dockerd/vpnkit/krun -> the container runtime serving prod and staging.
# cloudflared                    -> the tunnels serving api.tracelearn.app.
# Runner.Listener/Runner.Worker  -> the CI runner itself.
DENY_PATTERN='com\.docker|dockerd|containerd|vpnkit|krun|Docker Desktop|cloudflared|Runner\.Listener|Runner\.Worker|/sbin/launchd|sshd|/usr/bin/ssh'

# The _work root for this slot. RUNNER_WORKSPACE is set by the runner; fall back to the
# hook's own location only if it is absent.
work_root="${RUNNER_WORKSPACE:-}"
if [[ -z "$work_root" && -n "${GITHUB_WORKSPACE:-}" ]]; then
  work_root="$GITHUB_WORKSPACE"
fi
if [[ -z "$work_root" ]]; then
  echo "runner-reap: no RUNNER_WORKSPACE/GITHUB_WORKSPACE; refusing to guess a scope." >&2
  exit 0
fi
# Climb to the _work* directory that contains this workspace, so siblings in the same slot
# are covered but OTHER slots are not.
slot_root="$work_root"
while [[ "$slot_root" != "/" && ! "$(basename "$slot_root")" =~ ^_work[0-9]*$ ]]; do
  slot_root="$(dirname "$slot_root")"
done
if [[ "$slot_root" == "/" ]]; then
  echo "runner-reap: could not resolve a _work root from '$work_root'; doing nothing." >&2
  exit 0
fi
# Resolve symlinks: lsof reports PHYSICAL paths, so on macOS a /tmp/... scope would never
# match a cwd reported as /private/tmp/... and the reaper would silently do nothing.
if [[ -d "$slot_root" ]]; then
  slot_root="$(cd "$slot_root" 2>/dev/null && pwd -P)" || true
fi

# One lsof call for every cwd this user owns, rather than one per pid.
declare -a candidates=()
while IFS= read -r line; do
  case "$line" in
    p*) cur_pid="${line#p}" ;;
    n*)
      cur_cwd="${line#n}"
      if [[ "$cur_cwd" == "$slot_root"/* || "$cur_cwd" == "$slot_root" ]]; then
        candidates+=("$cur_pid")
      fi
      ;;
  esac
done < <(lsof -u "$(id -un)" -a -d cwd -n -P -F pn 2>/dev/null)

reaped=0
inspected=0
# `${candidates[@]}` on an empty array is an UNBOUND VARIABLE error under `set -u`, which
# would abort the hook on the common case of nothing to reap.
for pid in ${candidates[@]+"${candidates[@]}"}; do
  [[ "$pid" == "$$" ]] && continue
  cmd="$(ps -o command= -p "$pid" 2>/dev/null)" || continue
  [[ -z "$cmd" ]] && continue
  age="$(etime_to_seconds "$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')")"
  [[ -z "$age" ]] && continue
  inspected=$((inspected + 1))

  if [[ "$cmd" =~ $DENY_PATTERN ]]; then
    continue
  fi
  if (( age < MIN_AGE_SECONDS )); then
    continue
  fi
  # Never signal an ancestor of this hook.
  ancestor="$$"
  is_ancestor=0
  while [[ -n "$ancestor" && "$ancestor" != "1" && "$ancestor" != "0" ]]; do
    [[ "$ancestor" == "$pid" ]] && { is_ancestor=1; break; }
    ancestor="$(ps -o ppid= -p "$ancestor" 2>/dev/null | tr -d ' ')"
  done
  (( is_ancestor == 1 )) && continue

  short="$(printf '%.90s' "$cmd")"
  if (( DRY_RUN == 1 )); then
    echo "runner-reap: WOULD KILL pid=$pid age=${age}s cmd=$short"
    reaped=$((reaped + 1))
    continue
  fi

  echo "runner-reap: killing leftover pid=$pid age=${age}s cmd=$short"
  kill_tree "$pid"
  reaped=$((reaped + 1))
done

if (( reaped > 0 )); then
  echo "runner-reap: scope=$slot_root inspected=$inspected reaped=$reaped (dry_run=$DRY_RUN)"
fi
exit 0
