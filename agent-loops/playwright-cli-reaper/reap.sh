#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

MODE="orphan"
DRY_RUN=0
STATUS_ONLY=0
GRACE_SECONDS="${PLAYWRIGHT_REAPER_GRACE_SECONDS:-5}"
MIN_AGE_MINUTES="${PLAYWRIGHT_REAPER_MIN_AGE_MINUTES:-5}"
MAX_AGE_HOURS="${PLAYWRIGHT_REAPER_MAX_AGE_HOURS:-0}"
PS_FILE="${PLAYWRIGHT_REAPER_PS_FILE:-}"
TMP_ROOT="${PLAYWRIGHT_REAPER_TMP_ROOT:-${TMPDIR:-/tmp}}"

usage() {
  cat <<'HELP'
Usage:
  playwright-reap
  playwright-reap --dry-run
  playwright-reap --max-age-hours N [--dry-run]
  playwright-reap --force [--dry-run]
  playwright-reap --status

Default mode terminates only Playwright-owned browser processes whose PPID is
1. This is the safe recurring/manual backstop and does not stop a browser that
still has a live Playwright CLI daemon parent.

--max-age-hours N also terminates Playwright CLI daemons that have been
running for more than N hours, together with every process descended from them
(their headless Chrome trees). This is what the hourly LaunchAgent runs: a
detached daemon that is still alive hours later has been abandoned by its agent.

--force first runs `playwright-cli kill-all`, then terminates every remaining
Playwright CLI daemon and browser process identified by Playwright-specific
command-line markers.
HELP
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --force)
      MODE="force"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --max-age-hours)
      MAX_AGE_HOURS="${2:-}"
      shift 2 || { echo "--max-age-hours needs a value." >&2; exit 2; }
      ;;
    --status)
      STATUS_ONLY=1
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

if ! [[ "$GRACE_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "PLAYWRIGHT_REAPER_GRACE_SECONDS must be a non-negative integer." >&2
  exit 2
fi

if ! [[ "$MIN_AGE_MINUTES" =~ ^[0-9]+$ ]]; then
  echo "PLAYWRIGHT_REAPER_MIN_AGE_MINUTES must be a non-negative integer." >&2
  exit 2
fi

if ! [[ "$MAX_AGE_HOURS" =~ ^[0-9]+$ ]]; then
  echo "--max-age-hours / PLAYWRIGHT_REAPER_MAX_AGE_HOURS must be a non-negative integer." >&2
  exit 2
fi

snapshot_file="$(mktemp -t playwright-reaper-processes.XXXXXX)"
trap 'rm -f "$snapshot_file"' EXIT

take_snapshot() {
  if [[ -n "$PS_FILE" ]]; then
    cp "$PS_FILE" "$snapshot_file"
  else
    ps -Aww -o pid=,ppid=,etime=,command= >"$snapshot_file"
  fi
}

# ps etime is [[dd-]hh:]mm:ss; print it as whole seconds.
etime_seconds() {
  local etime="$1"
  local days=0
  local hours=0
  local minutes=0
  local seconds=0
  local IFS=":"
  local parts=()

  if [[ "$etime" == *-* ]]; then
    days="${etime%%-*}"
    etime="${etime#*-}"
  fi
  read -r -a parts <<<"$etime"
  case "${#parts[@]}" in
    3) hours="${parts[0]}"; minutes="${parts[1]}"; seconds="${parts[2]}" ;;
    2) minutes="${parts[0]}"; seconds="${parts[1]}" ;;
    *) printf '0\n'; return 0 ;;
  esac
  printf '%d\n' $(( 10#$days * 86400 + 10#$hours * 3600 + 10#$minutes * 60 + 10#$seconds ))
}

is_cli_daemon() {
  local command="$1"
  case "$command" in
    *"/playwright/cli.js run-cli-server"*|*"/playwright-core/lib/entry/cliDaemon.js"*|*"cliDaemon.js"*"--daemon-session"*|*"dashboardApp.js"*)
      return 0
      ;;
  esac
  return 1
}

has_playwright_profile() {
  local command="$1"
  case "$command" in
    *"/playwright_chromiumdev_profile-"*|*"/ms-playwright/daemon/"*)
      return 0
      ;;
  esac
  return 1
}

is_browser_process() {
  local command="$1"
  case "$command" in
    *"/Google Chrome"*|*"/Chromium"*|*"/chrome"*|*"chrome_crashpad_handler"*)
      return 0
      ;;
  esac
  return 1
}

process_kind() {
  local command="$1"
  if is_cli_daemon "$command"; then
    printf '%s\n' "daemon"
  elif [[ "$command" == *"Google Chrome Helper"* || "$command" == *"Chromium Helper"* ]]; then
    printf '%s\n' "browser-helper"
  elif is_browser_process "$command"; then
    printf '%s\n' "browser"
  else
    printf '%s\n' "profile-process"
  fi
}

TARGET_PIDS=()
TARGET_DETAILS=()

add_target() {
  local pid="$1"
  local detail="$2"
  local existing=""
  for existing in "${TARGET_PIDS[@]:-}"; do
    if [[ "$existing" == "$pid" ]]; then
      return 0
    fi
  done
  TARGET_PIDS+=("$pid")
  TARGET_DETAILS+=("$detail")
}

collect_targets() {
  local pid=""
  local ppid=""
  local etime=""
  local command=""
  local kind=""

  TARGET_PIDS=()
  TARGET_DETAILS=()

  while read -r pid ppid etime command; do
    [[ "$pid" =~ ^[0-9]+$ && "$ppid" =~ ^[0-9]+$ ]] || continue
    kind="$(process_kind "$command")"

    if [[ "$MODE" == "force" ]]; then
      if is_cli_daemon "$command" || has_playwright_profile "$command"; then
        add_target "$pid" "pid=${pid} ppid=${ppid} kind=${kind}"
      fi
      continue
    fi

    if [[ "$ppid" == "1" ]] && has_playwright_profile "$command" && is_browser_process "$command"; then
      add_target "$pid" "pid=${pid} ppid=${ppid} kind=${kind}"
    fi

    if (( MAX_AGE_HOURS > 0 )) && is_cli_daemon "$command" \
      && (( $(etime_seconds "$etime") > MAX_AGE_HOURS * 3600 )); then
      add_target "$pid" "pid=${pid} ppid=${ppid} kind=${kind} age=${etime}"
    fi
  done <"$snapshot_file"

  if [[ "$MODE" != "force" ]] && (( MAX_AGE_HOURS > 0 )); then
    add_descendants
  fi
}

# Pull in every process descended from a target (a stale daemon's Chrome tree).
add_descendants() {
  local pid=""
  local ppid=""
  local etime=""
  local command=""
  local before=-1

  while (( before != ${#TARGET_PIDS[@]} )); do
    before=${#TARGET_PIDS[@]}
    while read -r pid ppid etime command; do
      [[ "$pid" =~ ^[0-9]+$ && "$ppid" =~ ^[0-9]+$ ]] || continue
      if is_target "$ppid"; then
        add_target "$pid" "pid=${pid} ppid=${ppid} kind=$(process_kind "$command")"
      fi
    done <"$snapshot_file"
  done
}

is_target() {
  local candidate="$1"
  local existing=""
  for existing in "${TARGET_PIDS[@]:-}"; do
    [[ "$existing" == "$candidate" ]] && return 0
  done
  return 1
}

print_status() {
  local pid=""
  local ppid=""
  local etime=""
  local command=""
  local daemon_count=0
  local browser_count=0
  local orphan_browser_count=0

  while read -r pid ppid etime command; do
    [[ "$pid" =~ ^[0-9]+$ && "$ppid" =~ ^[0-9]+$ ]] || continue
    if is_cli_daemon "$command"; then
      daemon_count=$(( daemon_count + 1 ))
    fi
    if has_playwright_profile "$command" && is_browser_process "$command"; then
      browser_count=$(( browser_count + 1 ))
      if [[ "$ppid" == "1" ]]; then
        orphan_browser_count=$(( orphan_browser_count + 1 ))
      fi
    fi
  done <"$snapshot_file"

  printf 'playwright_cli_daemons=%d\n' "$daemon_count"
  printf 'playwright_browser_processes=%d\n' "$browser_count"
  printf 'orphan_browser_processes=%d\n' "$orphan_browser_count"
}

run_upstream_kill_all() {
  local cli=""
  cli="$(command -v playwright-cli 2>/dev/null || true)"
  if [[ -z "$cli" ]]; then
    echo "playwright-cli is not installed; continuing with marker-based cleanup." >&2
    return 0
  fi

  if (( DRY_RUN == 1 )); then
    echo "would run: $cli kill-all"
    return 0
  fi

  "$cli" kill-all || echo "playwright-cli kill-all failed; continuing with marker-based cleanup." >&2
}

terminate_targets() {
  local pid=""
  local detail=""
  local second=0
  local remaining=()

  if [[ "${#TARGET_PIDS[@]}" -eq 0 ]]; then
    echo "No matching Playwright processes found."
    return 0
  fi

  for detail in "${TARGET_DETAILS[@]}"; do
    if (( DRY_RUN == 1 )); then
      echo "would terminate: $detail"
    else
      echo "terminating: $detail"
    fi
  done

  if (( DRY_RUN == 1 )); then
    return 0
  fi

  kill -TERM "${TARGET_PIDS[@]}" 2>/dev/null || true

  while (( second < GRACE_SECONDS )); do
    remaining=()
    for pid in "${TARGET_PIDS[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        remaining+=("$pid")
      fi
    done
    if [[ "${#remaining[@]}" -eq 0 ]]; then
      return 0
    fi
    sleep 1
    second=$(( second + 1 ))
  done

  remaining=()
  for pid in "${TARGET_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      remaining+=("$pid")
    fi
  done
  if [[ "${#remaining[@]}" -gt 0 ]]; then
    echo "force killing remaining Playwright processes: ${remaining[*]}"
    kill -KILL "${remaining[@]}" 2>/dev/null || true
  fi
}

cleanup_stale_temp_profiles() {
  local profile_dir=""
  local profile_name=""
  local removed=0

  [[ -d "$TMP_ROOT" ]] || return 0

  while IFS= read -r -d '' profile_dir; do
    profile_name="$(basename "$profile_dir")"
    [[ "$profile_name" == playwright_chromiumdev_profile-* ]] || continue

    if grep -F -- "$profile_dir" "$snapshot_file" >/dev/null 2>&1; then
      continue
    fi

    if (( DRY_RUN == 1 )); then
      echo "would remove stale temp profile: $profile_dir"
    else
      echo "removing stale temp profile: $profile_dir"
      rm -rf -- "$profile_dir"
    fi
    removed=$(( removed + 1 ))
  done < <(find "$TMP_ROOT" -maxdepth 1 -type d -name 'playwright_chromiumdev_profile-*' -mmin "+${MIN_AGE_MINUTES}" -print0 2>/dev/null)

  if [[ "$removed" -eq 0 ]]; then
    echo "No stale Playwright temp profiles found."
  fi
}

take_snapshot

if (( STATUS_ONLY == 1 )); then
  print_status
  exit 0
fi

if [[ "$MODE" == "force" ]]; then
  run_upstream_kill_all
  if (( DRY_RUN == 0 )); then
    sleep 1
    take_snapshot
  fi
fi

collect_targets
terminate_targets

if (( DRY_RUN == 0 )); then
  take_snapshot
fi
cleanup_stale_temp_profiles

if (( DRY_RUN == 0 )); then
  take_snapshot
  collect_targets
  if [[ "${#TARGET_PIDS[@]}" -gt 0 ]]; then
    echo "ERROR: ${#TARGET_PIDS[@]} matching Playwright process(es) remain." >&2
    exit 1
  fi
fi
