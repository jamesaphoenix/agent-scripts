#!/usr/bin/env bash
# Dev-laptop Docker janitor for the MacBook Pro.
#
# The Mac Studio has agent-loops/docker-cleanup, built for a shared PROD daemon: it
# protects release images and trims BuildKit cache. A laptop accumulates a different
# kind of junk - dangling image layers, anonymous volumes from `docker compose up`
# cycles, stale build cache - and has no prod stacks to protect. This loop only ever
# removes things nothing references:
#
#   1. dangling images            (untagged layers no container uses)
#   2. BuildKit cache             older than BUILD_CACHE_MAX_AGE
#   3. ANONYMOUS dangling volumes older than VOLUME_MAX_AGE (64-hex names, created
#                                  implicitly by images that declare VOLUME; throwaway
#                                  by design)
#
# NEVER removed: tagged images, any container (running or stopped), and NAMED volumes,
# even when dangling. A named volume left behind by `docker compose down` is usually a
# dev database someone expects to survive; those are listed in the log for a human.
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="${DEV_DOCKER_CLEANUP_STATE_DIR:-$ROOT_DIR/state/dev-docker-cleanup}"
BUILD_CACHE_MAX_AGE="${DEV_DOCKER_CLEANUP_BUILD_CACHE_MAX_AGE:-168h}"   # 7 days
VOLUME_MAX_AGE_DAYS="${DEV_DOCKER_CLEANUP_VOLUME_MAX_AGE_DAYS:-30}"
DRY_RUN="${DRY_RUN:-0}"
MODE="run"

usage() {
  cat <<'HELP'
Usage:
  cleanup-dev-docker.sh              reclaim dangling images, old build cache, old anonymous volumes
  cleanup-dev-docker.sh --dry-run    report what would be removed, change nothing
  cleanup-dev-docker.sh --status     docker system df plus the last run summary
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --status) MODE="status"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$STATE_DIR"

log() {
  printf '%s dev-docker-cleanup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

if ! docker info >/dev/null 2>&1; then
  # Docker Desktop is often simply not running on a laptop. Not an error.
  log "docker daemon not running - nothing to do"
  exit 0
fi

if [ "$MODE" = "status" ]; then
  docker system df
  echo
  if [ -f "$STATE_DIR/last-summary.json" ]; then
    echo "last run:"
    sed 's/^/  /' "$STATE_DIR/last-summary.json"
  else
    echo "last run: none"
  fi
  exit 0
fi

if (( DRY_RUN )); then
  log "DRY RUN - nothing will be removed"
fi

docker_data_free_gb() {
  df -g /System/Volumes/Data 2>/dev/null | awk 'NR==2 { print $4 }'
}

free_before="$(docker_data_free_gb)"
log "free space before: ${free_before}G"

# 1. Dangling images. `docker image prune` without -a touches only untagged layers that
#    no image tag or container references.
dangling_images="$(docker images -q -f dangling=true | sort -u | wc -l | tr -d ' ')"
images_reclaimed="0B"
if (( dangling_images > 0 )); then
  if (( DRY_RUN )); then
    log "would remove ${dangling_images} dangling image(s)"
  else
    images_reclaimed="$(docker image prune -f 2>/dev/null | awk '/Total reclaimed space/ { print $NF }')"
    log "removed ${dangling_images} dangling image(s), reclaimed ${images_reclaimed:-0B}"
  fi
else
  log "no dangling images"
fi

# 2. Build cache older than the threshold. Recent cache is what makes the next
#    `docker build` fast, so only stale entries go.
cache_reclaimed="0B"
if (( DRY_RUN )); then
  cache_candidates="$(docker system df -v 2>/dev/null | awk '/^CACHE ID/ { f=1; next } f && NF { n++ } END { print n+0 }')"
  log "would prune build cache older than ${BUILD_CACHE_MAX_AGE} (${cache_candidates} cache entr(ies) total)"
else
  cache_reclaimed="$(docker builder prune -f --filter "until=${BUILD_CACHE_MAX_AGE}" 2>/dev/null | awk '/Total.*reclaimed/ { print $NF }')"
  log "pruned build cache older than ${BUILD_CACHE_MAX_AGE}, reclaimed ${cache_reclaimed:-0B}"
fi

# 3. Anonymous dangling volumes older than the threshold. Anonymous = 64 hex chars,
#    which Docker generates for VOLUME directives and `-v /path` mounts with no name.
#    Named volumes are never touched; they are reported below instead.
cutoff_epoch="$(( $(date +%s) - VOLUME_MAX_AGE_DAYS * 86400 ))"
anon_removed=0
anon_kept_young=0
named_dangling=()
while read -r vol; do
  [ -n "$vol" ] || continue
  if [[ ! "$vol" =~ ^[0-9a-f]{64}$ ]]; then
    named_dangling+=("$vol")
    continue
  fi
  created="$(docker volume inspect -f '{{.CreatedAt}}' "$vol" 2>/dev/null)"
  # CreatedAt looks like 2026-08-27T13:23:42Z; BSD date needs the format spelled out.
  created_epoch="$(date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "${created%%.*}" +%s 2>/dev/null || echo 0)"
  if (( created_epoch == 0 || created_epoch > cutoff_epoch )); then
    anon_kept_young=$(( anon_kept_young + 1 ))
    continue
  fi
  if (( DRY_RUN )); then
    anon_removed=$(( anon_removed + 1 ))
    continue
  fi
  if docker volume rm "$vol" >/dev/null 2>&1; then
    anon_removed=$(( anon_removed + 1 ))
  else
    log "failed to remove anonymous volume ${vol}"
  fi
done < <(docker volume ls -q -f dangling=true)

if (( DRY_RUN )); then
  log "would remove ${anon_removed} anonymous volume(s) older than ${VOLUME_MAX_AGE_DAYS}d (${anon_kept_young} younger, kept)"
else
  log "removed ${anon_removed} anonymous volume(s) older than ${VOLUME_MAX_AGE_DAYS}d (${anon_kept_young} younger, kept)"
fi

if (( ${#named_dangling[@]} > 0 )); then
  log "${#named_dangling[@]} NAMED volume(s) are unattached and were left alone (review by hand):"
  for vol in "${named_dangling[@]}"; do
    log "  $vol"
  done
fi

free_after="$(docker_data_free_gb)"
log "free space after: ${free_after}G"

{
  printf '{\n'
  printf '  "ranAt": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "dryRun": %s,\n' "$( (( DRY_RUN )) && echo true || echo false )"
  printf '  "danglingImages": %s,\n' "$dangling_images"
  printf '  "imagesReclaimed": "%s",\n' "${images_reclaimed:-0B}"
  printf '  "buildCacheReclaimed": "%s",\n' "${cache_reclaimed:-0B}"
  printf '  "anonymousVolumesRemoved": %s,\n' "$anon_removed"
  printf '  "namedDanglingVolumesKept": %s,\n' "${#named_dangling[@]}"
  printf '  "freeGbBefore": %s,\n' "${free_before:-0}"
  printf '  "freeGbAfter": %s\n' "${free_after:-0}"
  printf '}\n'
} >"$STATE_DIR/last-summary.json"

exit 0
