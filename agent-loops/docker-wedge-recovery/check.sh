#!/usr/bin/env bash
# Detect a wedged Docker Desktop daemon on the Mac Studio and recover it by
# rebooting the HOST.
#
# ── The wedge (measured 2026-08-04) ─────────────────────────────────────────────
#
# Docker Desktop periodically enters a state where the daemon looks alive from
# every cheap angle - `docker ps`, `docker exec` and `docker pull` all answer
# normally, and the socket still returns HTTP 200 on /_ping - but EVERY NEW
# CONTAINER START HANGS FOREVER. Containers pile up in `Created` and never run.
# A whole-VM `sync` also hangs. Nothing that only reads daemon state can see
# this, which is why the probe below actually starts a container.
#
# ROOT CAUSE: accumulated HOST-SIDE Docker Desktop state on the macOS side. Not
# the Linux VM, not the hypervisor, not the workload.
#
# ── Why reboot, and why NEVER purge ─────────────────────────────────────────────
#
# Rebooting the host is the only recovery proven to work. During the incident
# the VM disk (~/Library/Containers/com.docker.docker/Data/vms) was purged
# first: it bought about 40 minutes before the wedge returned and destroyed
# 218GB of images and volumes for nothing. THIS SCRIPT MUST NEVER TOUCH THE VM
# DATA DIRECTORY. If you are tempted to add a purge here, re-read this comment.
#
# ── Things that lie in this state ───────────────────────────────────────────────
#
#   * `docker desktop status` reports "running" when there is no socket at all.
#   * `docker desktop stop` / `docker desktop start` hang.
# Neither is used here. The socket is checked directly with curl --unix-socket.
#
# ── Why rebooting is safe unattended ────────────────────────────────────────────
#
# On the Mac Studio: FileVault is Off and auto-login is enabled, so the host
# boots back to a logged-in session by itself; Docker Desktop AutoStart brings
# the daemon up and `restart: always` brings the prod/staging stacks back. That
# was verified on 2026-08-04. Passwordless reboot comes from
# /etc/sudoers.d/reboot-nopasswd (jamesphoenix ALL=(root) NOPASSWD:
# /sbin/shutdown, /sbin/reboot).
#
# macOS `shutdown` has NO `-c` cancel flag and `shutdown -h +N` runs in the
# FOREGROUND, so only the immediate form is used: `sudo -n /sbin/shutdown -r now`.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/bounded.sh
source "$SCRIPT_DIR/lib/bounded.sh"

STATE_DIR="${DOCKER_WEDGE_STATE_DIR:-$ROOT_DIR/state/docker-wedge-recovery}"
DISABLE_FLAG="${DOCKER_WEDGE_DISABLE_FILE:-$STATE_DIR/disabled}"
STATUS_FILE="$STATE_DIR/last-status.json"
REBOOT_FILE="$STATE_DIR/last-reboot.json"
PENDING_FILE="$STATE_DIR/pending-verification"
HISTORY_FILE="$STATE_DIR/reboot-history"
VERIFY_FILE="$STATE_DIR/last-post-boot-verification.json"

PROBE_IMAGE="${DOCKER_WEDGE_PROBE_IMAGE:-alpine:3.20}"
PROBE_TIMEOUT_SECONDS="${DOCKER_WEDGE_PROBE_TIMEOUT_SECONDS:-60}"
DOCKER_PS_TIMEOUT_SECONDS="${DOCKER_WEDGE_DOCKER_PS_TIMEOUT_SECONDS:-20}"
PULL_TIMEOUT_SECONDS="${DOCKER_WEDGE_PULL_TIMEOUT_SECONDS:-120}"
PING_TIMEOUT_SECONDS="${DOCKER_WEDGE_PING_TIMEOUT_SECONDS:-10}"

MIN_REBOOT_INTERVAL_MINUTES="${DOCKER_WEDGE_MIN_REBOOT_INTERVAL_MINUTES:-60}"
MAX_REBOOTS_PER_DAY="${DOCKER_WEDGE_MAX_REBOOTS_PER_DAY:-3}"

# Reboot is only ever issued on these hosts, as these users. Anywhere else the
# script still probes and reports, but stops short of the reboot - so running
# it by hand on the MacBook can never take the MacBook down.
ALLOWED_REBOOT_HOSTS="${DOCKER_WEDGE_ALLOW_REBOOT_HOSTS:-Jamess-Mac-Studio Jamess-Mac-Studio.local JamessMacStudio.fritz.box}"
ALLOWED_REBOOT_USERS="${DOCKER_WEDGE_ALLOW_REBOOT_USERS:-jamesphoenix}"

# Containers expected back after a reboot, space-separated substrings matched
# against running container names during post-boot verification.
EXPECTED_STACKS="${DOCKER_WEDGE_EXPECTED_STACKS:-octospark-prod-live octospark-staging-live}"

mkdir -p "$STATE_DIR"

log() {
  printf '%s docker-wedge-recovery: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

json_escape() {
  sed \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e 's/	/\\t/g'
}

write_status() {
  local status="$1"
  local detail="$2"
  {
    printf '{\n'
    printf '  "status": "%s",\n' "$(printf '%s' "$status" | json_escape)"
    printf '  "detail": "%s",\n' "$(printf '%s' "$detail" | json_escape)"
    printf '  "checkedAt": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '}\n'
  } >"$STATUS_FILE"
}

lowercase() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

host_candidates() {
  local name=""
  for name in \
    "$(hostname 2>/dev/null || true)" \
    "$(hostname -s 2>/dev/null || true)" \
    "$(scutil --get LocalHostName 2>/dev/null || true)" \
    "$(scutil --get ComputerName 2>/dev/null || true)"; do
    if [[ -n "$name" ]]; then
      printf '%s\n' "$name"
      # A Studio that answers "Jamess-Mac-Studio.local" should also match the
      # bare registry host entry, and vice versa.
      printf '%s\n' "${name%.local}"
    fi
  done
}

# The load-bearing safety check. Reboot requires BOTH a known host name and a
# known user; anything unrecognised (the MacBook, a worktree, CI) is refused.
reboot_allowed_here() {
  local current_user=""
  local allowed=""
  local candidate=""
  local user_ok=0

  current_user="$(lowercase "$(id -un)")"
  for allowed in $ALLOWED_REBOOT_USERS; do
    if [[ "$current_user" == "$(lowercase "$allowed")" ]]; then
      user_ok=1
      break
    fi
  done

  if (( user_ok == 0 )); then
    return 1
  fi

  while IFS= read -r candidate; do
    for allowed in $ALLOWED_REBOOT_HOSTS; do
      if [[ "$(lowercase "$candidate")" == "$(lowercase "$allowed")" ]]; then
        return 0
      fi
    done
  done < <(host_candidates)

  return 1
}

docker_socket_path() {
  local candidate=""
  for candidate in "${DOCKER_WEDGE_SOCKET:-}" "$HOME/.docker/run/docker.sock" "/var/run/docker.sock"; do
    if [[ -n "$candidate" && -S "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

socket_pings() {
  local socket=""
  if ! socket="$(docker_socket_path)"; then
    return 1
  fi

  # curl's own --max-time is the primary bound; bounded_run is the backstop for
  # the case where the connect itself never returns to curl's timer.
  bounded_run "$(( PING_TIMEOUT_SECONDS + 5 ))" \
    curl --silent --show-error --fail --max-time "$PING_TIMEOUT_SECONDS" \
    --unix-socket "$socket" http://localhost/_ping >/dev/null 2>&1
}

docker_ps_works() {
  bounded_run "$DOCKER_PS_TIMEOUT_SECONDS" docker ps --format '{{.Names}}' >/dev/null 2>&1
}

ensure_probe_image() {
  if bounded_run "$DOCKER_PS_TIMEOUT_SECONDS" docker image inspect "$PROBE_IMAGE" >/dev/null 2>&1; then
    return 0
  fi

  log "probe image ${PROBE_IMAGE} is missing locally; pulling (bounded ${PULL_TIMEOUT_SECONDS}s)"
  bounded_run "$PULL_TIMEOUT_SECONDS" docker pull "$PROBE_IMAGE" >/dev/null 2>&1
}

# The only check that can see the wedge: actually START a container.
container_start_works() {
  bounded_run "$PROBE_TIMEOUT_SECONDS" \
    docker run --rm "$PROBE_IMAGE" true >/dev/null 2>&1
}

now_epoch() {
  date +%s
}

prune_reboot_history() {
  local cutoff=""
  local entry=""
  local kept=""

  if [[ ! -f "$HISTORY_FILE" ]]; then
    return 0
  fi

  cutoff=$(( $(now_epoch) - 86400 ))
  kept="$(mktemp -t docker-wedge-recovery-history.XXXXXX)"
  while IFS= read -r entry; do
    if [[ "$entry" =~ ^[0-9]+$ ]] && (( entry >= cutoff )); then
      printf '%s\n' "$entry" >>"$kept"
    fi
  done <"$HISTORY_FILE"
  mv "$kept" "$HISTORY_FILE"
}

reboots_in_last_day() {
  local count=0
  local entry=""

  if [[ -f "$HISTORY_FILE" ]]; then
    while IFS= read -r entry; do
      if [[ -n "$entry" ]]; then
        count=$(( count + 1 ))
      fi
    done <"$HISTORY_FILE"
  fi

  printf '%s\n' "$count"
}

minutes_since_last_reboot() {
  local last=""

  if [[ ! -s "$HISTORY_FILE" ]]; then
    printf '%s\n' "999999"
    return 0
  fi

  last="$(tail -n 1 "$HISTORY_FILE")"
  if [[ ! "$last" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "999999"
    return 0
  fi

  printf '%s\n' "$(( ( $(now_epoch) - last ) / 60 ))"
}

record_reboot() {
  local reason="$1"
  local now_iso=""
  now_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  printf '%s\n' "$(now_epoch)" >>"$HISTORY_FILE"
  {
    printf '{\n'
    printf '  "reason": "%s",\n' "$(printf '%s' "$reason" | json_escape)"
    printf '  "rebootedAt": "%s",\n' "$now_iso"
    printf '  "rebootsInLastDay": %s\n' "$(reboots_in_last_day)"
    printf '}\n'
  } >"$REBOOT_FILE"
  printf '%s\n' "$now_iso" >"$PENDING_FILE"
}

# Runs on the first healthy pass AFTER this loop rebooted the host, so the
# reboot is confirmed to have actually fixed things rather than assumed.
verify_after_boot() {
  local rebooted_at=""
  local ping_state="failed"
  local running=""
  local stack=""
  local missing=""
  local found=""

  rebooted_at="$(cat "$PENDING_FILE" 2>/dev/null || true)"

  if socket_pings; then
    ping_state="ok"
  fi

  running="$(mktemp -t docker-wedge-recovery-running.XXXXXX)"
  bounded_run "$DOCKER_PS_TIMEOUT_SECONDS" docker ps --format '{{.Names}}' >"$running" 2>/dev/null || true

  for stack in $EXPECTED_STACKS; do
    if grep -q -- "$stack" "$running" 2>/dev/null; then
      found="${found:+${found} }${stack}"
    else
      missing="${missing:+${missing} }${stack}"
    fi
  done
  rm -f "$running"

  {
    printf '{\n'
    printf '  "rebootedAt": "%s",\n' "$(printf '%s' "$rebooted_at" | json_escape)"
    printf '  "verifiedAt": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "socketPing": "%s",\n' "$ping_state"
    printf '  "stacksRunning": "%s",\n' "$(printf '%s' "$found" | json_escape)"
    printf '  "stacksMissing": "%s"\n' "$(printf '%s' "$missing" | json_escape)"
    printf '}\n'
  } >"$VERIFY_FILE"

  if [[ -n "$missing" ]]; then
    log "post-boot verification: socket ping ${ping_state}, container start ok, MISSING stacks: ${missing}"
  else
    log "post-boot verification: socket ping ${ping_state}, container start ok, all expected stacks running"
  fi

  rm -f "$PENDING_FILE"
}

reboot_host() {
  local reason="$1"

  if ! reboot_allowed_here; then
    log "WOULD REBOOT (${reason}) but this host/user is not an allowed reboot target - user=$(id -un) host=$(hostname 2>/dev/null || echo unknown)"
    write_status "wedged-not-recovered" "Docker wedge detected but reboot is not permitted on this host/user"
    return 1
  fi

  prune_reboot_history

  local count=""
  local since=""
  count="$(reboots_in_last_day)"
  since="$(minutes_since_last_reboot)"

  if (( count >= MAX_REBOOTS_PER_DAY )); then
    log "RATE LIMITED: ${count} reboots in the last 24h (cap ${MAX_REBOOTS_PER_DAY}). NOT rebooting - Docker is still wedged and needs a human. Reason was: ${reason}"
    write_status "rate-limited" "Docker wedge detected but the daily reboot cap (${MAX_REBOOTS_PER_DAY}) is reached"
    return 1
  fi

  if (( since < MIN_REBOOT_INTERVAL_MINUTES )); then
    log "RATE LIMITED: last reboot was ${since}m ago (minimum ${MIN_REBOOT_INTERVAL_MINUTES}m). NOT rebooting. Reason was: ${reason}"
    write_status "rate-limited" "Docker wedge detected but the last reboot was only ${since}m ago"
    return 1
  fi

  # Written BEFORE issuing the reboot: once shutdown fires, nothing else in this
  # process gets to run.
  record_reboot "$reason"
  write_status "rebooting" "$reason"
  log "REBOOTING HOST: ${reason} (reboot ${count} -> $(( count + 1 )) in the last 24h)"

  if ! sudo -n /sbin/shutdown -r now; then
    log "ERROR: sudo -n /sbin/shutdown -r now failed. Check /etc/sudoers.d/reboot-nopasswd."
    write_status "reboot-failed" "sudo -n /sbin/shutdown -r now failed"
    rm -f "$PENDING_FILE"
    return 1
  fi

  return 0
}

main() {
  if [[ -f "$DISABLE_FLAG" ]]; then
    log "disabled by ${DISABLE_FLAG} - skipping this run"
    exit 0
  fi

  if ! command -v docker >/dev/null 2>&1; then
    log "docker CLI not found - nothing to check"
    write_status "skipped" "docker CLI not found"
    exit 0
  fi

  # A daemon that is simply not up yet (Docker Desktop starts at user LOGIN on
  # macOS, and this loop runs right after boot) is not the wedge. Reboot would
  # be the worst possible response.
  if ! socket_pings && ! docker_ps_works; then
    log "Docker daemon is not answering at all (no socket ping, no docker ps). This is a down/starting daemon, NOT the wedge - not rebooting."
    write_status "daemon-unavailable" "Docker daemon is not answering; treated as down/starting, not wedged"
    exit 0
  fi

  if ! ensure_probe_image; then
    log "could not make probe image ${PROBE_IMAGE} available - result is inconclusive this run"
    write_status "inconclusive" "Probe image ${PROBE_IMAGE} unavailable"
    exit 0
  fi

  if container_start_works; then
    log "container start probe passed (${PROBE_IMAGE})"
    if [[ -f "$PENDING_FILE" ]]; then
      verify_after_boot
    fi
    write_status "ok" "Container start probe passed with ${PROBE_IMAGE}"
    exit 0
  fi

  local timed_out="$BOUNDED_RUN_TIMED_OUT"
  local reason=""

  if (( timed_out == 1 )); then
    reason="docker run ${PROBE_IMAGE} hung for more than ${PROBE_TIMEOUT_SECONDS}s"
  else
    reason="docker run ${PROBE_IMAGE} failed without hanging"
  fi

  log "container start probe FAILED: ${reason}"

  # Confirm the incident signature before doing anything drastic: the daemon
  # answers reads (socket ping and/or docker ps) while container starts hang.
  # A probe that fails FAST with a dead daemon is a different problem.
  local ping_ok=0
  local ps_ok=0
  if socket_pings; then
    ping_ok=1
  fi
  if docker_ps_works; then
    ps_ok=1
  fi

  if (( ping_ok == 0 && ps_ok == 0 )); then
    log "daemon stopped answering reads too - not the wedge signature, not rebooting"
    write_status "daemon-unavailable" "Probe failed and the daemon answers no reads; treated as down, not wedged"
    exit 1
  fi

  if (( timed_out == 0 )); then
    log "probe failed fast rather than hanging (socketPing=${ping_ok} dockerPs=${ps_ok}) - not the wedge signature, not rebooting"
    write_status "probe-failed" "Probe failed fast rather than hanging; not the wedge signature"
    exit 1
  fi

  reason="Docker wedge signature: ${reason} while socketPing=${ping_ok} dockerPs=${ps_ok}"
  reboot_host "$reason"
}

main "$@"
