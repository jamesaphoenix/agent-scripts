#!/usr/bin/env bash
# CI VM janitor: reclaims disk inside the Colima "ci" VM and returns it to the host.
#
# Why this exists separately from agent-loops/docker-cleanup:
#
# The Mac Studio runs TWO Docker daemons. docker-cleanup owns the host
# Docker Desktop daemon (production + staging compose stacks). The
# self-hosted GitHub Actions runners build against a second daemon inside
# the Colima "ci" VM (DOCKER_HOST=unix://~/.colima/ci/docker.sock), and
# nothing was cleaning that one. By 2026-09-08 it had accumulated 398
# orphaned anonymous volumes (36GB) and its sparse datadisk had grown to
# 108GB on the host, contributing to a 98%-full boot volume.
#
# Two distinct reclaims are needed, and the second is the one people miss:
#
#   1. Prune inside the guest    - frees space in the VM's filesystem.
#   2. fstrim the guest          - punches holes in the host's sparse
#                                  datadisk so the space actually returns
#                                  to macOS. Without this, step 1 frees
#                                  nothing the host can see: a raw sparse
#                                  image only ever grows.
#
# It also reaps orphaned ephemeral CI containers on the HOST daemon. Those
# are spawned detached by CI scripts that clean up via a shell EXIT trap;
# when a workflow is cancelled or timed out the shell is SIGKILLed, the
# trap never runs, and the container is left holding an authenticated
# tunnel open indefinitely.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Shared with docker-cleanup deliberately: one owner, no drift.
source "$SCRIPT_DIR/../docker-cleanup/lib/lock.sh"
source "$SCRIPT_DIR/../docker-cleanup/lib/timeout.sh"

DRY_RUN="${DRY_RUN:-0}"
COLIMA_PROFILE="${CI_VM_CLEANUP_PROFILE:-ci}"
BUILDER_CACHE_UNTIL="${CI_VM_CLEANUP_BUILDER_UNTIL:-168h}"
LOCK_DIR="${CI_VM_CLEANUP_LOCK_DIR:-/tmp/ci-vm-cleanup.lock}"
LOCK_TIMEOUT_SECONDS="${CI_VM_CLEANUP_LOCK_TIMEOUT_SECONDS:-60}"
LOCK_MISSING_PID_GRACE_SECONDS="${CI_VM_CLEANUP_LOCK_MISSING_PID_GRACE_SECONDS:-15}"
COMMAND_TIMEOUT_SECONDS="${CI_VM_CLEANUP_COMMAND_TIMEOUT_SECONDS:-900}"

# Ephemeral CI containers on the HOST daemon that are safe to reap once
# their creating process is gone. Space-separated docker name filters.
ORPHAN_PATTERNS="${CI_VM_CLEANUP_ORPHAN_PATTERNS:-tracelearn-mobile-readiness-}"
# Belt and braces: never reap anything younger than this, even if the PID
# check says orphaned, so a run that recycles a PID is never hit.
ORPHAN_MIN_AGE_HOURS="${CI_VM_CLEANUP_ORPHAN_MIN_AGE_HOURS:-3}"

if [[ "$DRY_RUN" != "0" && "$DRY_RUN" != "1" ]]; then
  echo "DRY_RUN must be 0 or 1, got '$DRY_RUN'."
  exit 1
fi

if ! [[ "$ORPHAN_MIN_AGE_HOURS" =~ ^[0-9]+$ ]]; then
  echo "CI_VM_CLEANUP_ORPHAN_MIN_AGE_HOURS must be an integer, got '$ORPHAN_MIN_AGE_HOURS'."
  exit 1
fi

CI_VM_CLEANUP_DISABLE_FILE="${CI_VM_CLEANUP_DISABLE_FILE:-}"
if [[ -n "$CI_VM_CLEANUP_DISABLE_FILE" && -e "$CI_VM_CLEANUP_DISABLE_FILE" ]]; then
  echo "Disabled by $CI_VM_CLEANUP_DISABLE_FILE - skipping this run."
  exit 0
fi

for binary in docker colima; do
  command -v "$binary" >/dev/null 2>&1 || {
    echo "$binary is required"
    exit 1
  }
done

LOCK_ACQUIRED=0
cleanup() {
  if [[ "$LOCK_ACQUIRED" == "1" ]]; then
    lock_release "$LOCK_DIR"
  fi
}
trap cleanup EXIT

if ! lock_acquire "$LOCK_DIR" "$LOCK_TIMEOUT_SECONDS" "$LOCK_MISSING_PID_GRACE_SECONDS"; then
  echo "Another CI VM cleanup is already in progress."
  exit 1
fi
LOCK_ACQUIRED=1

run_or_print() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

vm_exec() {
  run_with_timeout "$COMMAND_TIMEOUT_SECONDS" \
    colima ssh --profile "$COLIMA_PROFILE" -- "$@"
}

# ---------------------------------------------------------------------------
# 1. Reap orphaned ephemeral CI containers on the HOST daemon.
# ---------------------------------------------------------------------------

# Docker's ps formatter exposes .CreatedAt as "2026-09-07 16:44:23 +0100 BST".
# BSD date cannot parse the trailing zone abbreviation, so drop it.
docker_created_at_to_epoch() {
  local created_at="$1"
  local trimmed
  trimmed="$(printf '%s' "$created_at" | cut -d' ' -f1-3)"
  date -j -f '%Y-%m-%d %H:%M:%S %z' "$trimmed" +%s 2>/dev/null
}

# `kill -0 PID` cannot answer "is this process alive?" on its own: it fails
# with EPERM for a live process owned by another user, which is
# indistinguishable from ESRCH at the shell. A self-test on 2026-09-08 reaped
# a container whose creator was pid 1 for exactly this reason. `ps -p` reports
# existence regardless of ownership.
process_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  ps -p "$pid" -o pid= >/dev/null 2>&1
}

# Preferred mechanism: any script that spawns a detached container can opt in
# by labelling it com.jud.ephemeral=<kind>, com.jud.creator-pid=$$ and
# com.jud.creator-host=$(hostname -s). This reaps by label regardless of the
# container's name, so the convention works for scripts added later.
reap_labelled_orphans() {
  echo "== Host daemon: labelled ephemeral containers =="

  local this_host reaped=0
  this_host="$(hostname -s)"

  local container_id creator_pid kind name
  while IFS='|' read -r container_id name kind creator_pid; do
    [[ -n "$container_id" ]] || continue

    if ! [[ "$creator_pid" =~ ^[0-9]+$ ]]; then
      echo "  keep $name (no usable com.jud.creator-pid label)"
      continue
    fi

    if process_alive "$creator_pid"; then
      echo "  keep $name (creator pid $creator_pid still alive)"
      continue
    fi

    echo "  reap $name (kind=$kind, creator pid $creator_pid gone)"
    run_or_print docker rm -f "$container_id" >/dev/null || true
    reaped=$((reaped + 1))
  done < <(docker ps -q \
    --filter "label=com.jud.creator-host=$this_host" \
    2>/dev/null |
    xargs -r -I{} docker inspect {} --format \
      '{{.Id}}|{{.Name}}|{{index .Config.Labels "com.jud.ephemeral"}}|{{index .Config.Labels "com.jud.creator-pid"}}' \
      2>/dev/null | grep -v '|<no value>|' || true)

  echo "Reaped $reaped labelled container(s)."
}

reap_host_orphans() {
  echo "== Host daemon: orphaned ephemeral CI containers =="

  if ! docker info >/dev/null 2>&1; then
    echo "Host Docker daemon unavailable - skipping orphan reap."
    return 0
  fi

  local now reaped=0
  now="$(date +%s)"

  local pattern container_id name created_at created_epoch age_hours creator_pid
  for pattern in $ORPHAN_PATTERNS; do
    while IFS='|' read -r container_id name created_at; do
      [[ -n "$container_id" ]] || continue

      created_epoch="$(docker_created_at_to_epoch "$created_at")"
      if [[ -z "$created_epoch" ]]; then
        echo "  keep $name (unparseable creation time '$created_at')"
        continue
      fi

      age_hours=$(( (now - created_epoch) / 3600 ))
      if (( age_hours < ORPHAN_MIN_AGE_HOURS )); then
        echo "  keep $name (age ${age_hours}h < ${ORPHAN_MIN_AGE_HOURS}h)"
        continue
      fi

      # These are named <prefix>-<env>-<creator shell PID>. If that PID is
      # still alive the run is still going, whatever the container's age.
      creator_pid="${name##*-}"
      if process_alive "$creator_pid"; then
        echo "  keep $name (creator pid $creator_pid still alive)"
        continue
      fi

      echo "  reap $name (age ${age_hours}h, creator gone)"
      run_or_print docker rm -f "$container_id" >/dev/null || true
      reaped=$((reaped + 1))
    done < <(docker ps -a --filter "name=${pattern}" \
      --format '{{.ID}}|{{.Names}}|{{.CreatedAt}}' 2>/dev/null)
  done

  echo "Reaped $reaped orphaned container(s)."
}

# ---------------------------------------------------------------------------
# 2. Prune inside the CI VM, then 3. return the space to the host.
# ---------------------------------------------------------------------------

prune_ci_vm() {
  echo
  echo "== Colima '$COLIMA_PROFILE' VM =="

  if ! colima status --profile "$COLIMA_PROFILE" >/dev/null 2>&1; then
    echo "Colima profile '$COLIMA_PROFILE' is not running - skipping VM prune."
    return 0
  fi

  echo "-- before --"
  vm_exec df -h /var/lib/docker | tail -1 || true

  # Anonymous volumes only. `docker volume prune` without --all never
  # touches named volumes, which is what the long-lived CI service
  # containers (postgres, clickhouse, minio, langfuse) depend on.
  echo "-- anonymous volumes --"
  run_or_print vm_exec docker volume prune -f 2>&1 | tail -2 || true

  # Dangling images only. A full `image prune -a` would evict the base
  # image cache every CI run depends on and just move the cost to network.
  echo "-- dangling images --"
  run_or_print vm_exec docker image prune -f 2>&1 | tail -2 || true

  echo "-- build cache older than $BUILDER_CACHE_UNTIL --"
  run_or_print vm_exec docker builder prune -f --filter "until=$BUILDER_CACHE_UNTIL" 2>&1 | tail -1 || true

  echo "-- after --"
  vm_exec df -h /var/lib/docker | tail -1 || true
}

trim_ci_vm() {
  echo
  echo "== fstrim (return freed guest blocks to the host sparse image) =="

  if ! colima status --profile "$COLIMA_PROFILE" >/dev/null 2>&1; then
    echo "Colima profile '$COLIMA_PROFILE' is not running - skipping fstrim."
    return 0
  fi

  local datadisk="$HOME/.colima/_lima/_disks/${COLIMA_PROFILE}/datadisk"
  if [[ -f "$datadisk" ]]; then
    echo "host datadisk before: $(du -sh "$datadisk" 2>/dev/null | cut -f1)"
  fi

  run_or_print vm_exec sudo fstrim -v /var/lib/docker || true

  if [[ -f "$datadisk" ]]; then
    echo "host datadisk after:  $(du -sh "$datadisk" 2>/dev/null | cut -f1)"
  fi
}

BANNER_SUFFIX=""
if [[ "$DRY_RUN" == "1" ]]; then
  BANNER_SUFFIX=" [DRY RUN]"
fi
echo "CI VM cleanup starting ($(date '+%Y-%m-%d %H:%M:%S'))${BANNER_SUFFIX}"
echo

reap_labelled_orphans
echo
reap_host_orphans
prune_ci_vm
trim_ci_vm

echo
echo "== Host boot volume =="
df -h /System/Volumes/Data | tail -1

echo
echo "CI VM cleanup finished ($(date '+%Y-%m-%d %H:%M:%S'))"
