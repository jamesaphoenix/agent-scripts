#!/usr/bin/env bash
# Host-level Docker janitor: reclaims stale release images + build cache on a
# shared daemon (CI runners, production, and sibling products all live on the
# same Docker Desktop instance on the Mac Studio / same dockerd on a shared
# Hetzner box).
#
# This is cross-product by design and lives here, not in any one product repo,
# so there is one owner and no drift between what runs on each host.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/lib/lock.sh"
source "$SCRIPT_DIR/lib/timeout.sh"
source "$SCRIPT_DIR/lib/cache-storage-flag.sh"

DRY_RUN="${DRY_RUN:-0}"
KEEP_DEPLOY_ARTIFACTS="${KEEP_DEPLOY_ARTIFACTS:-20}"
BUILDER_CACHE_UNTIL="${BUILDER_CACHE_UNTIL:-24h}"
BUILDER_KEEP_STORAGE="${BUILDER_KEEP_STORAGE:-10GB}"
IMAGE_MIN_AGE_HOURS="${DOCKER_CLEANUP_IMAGE_MIN_AGE_HOURS:-24}"
# A scheduled host job must not inherit the CLI's selected CI context. Buildx
# also requires a context-backed builder to match the invocation's context.
DOCKER_CLEANUP_CONTEXT="${DOCKER_CLEANUP_CONTEXT:-default}"
export DOCKER_CONTEXT="$DOCKER_CLEANUP_CONTEXT"
BUILDER_NAME="${DOCKER_CLEANUP_BUILDER_NAME:-$DOCKER_CLEANUP_CONTEXT}"
PRUNE_STOPPED_CONTAINERS="${DOCKER_CLEANUP_PRUNE_STOPPED_CONTAINERS:-0}"
LOCK_DIR="${DOCKER_CLEANUP_LOCK_DIR:-/tmp/host-docker-cleanup.lock}"
LOCK_TIMEOUT_SECONDS="${DOCKER_CLEANUP_LOCK_TIMEOUT_SECONDS:-60}"
LOCK_MISSING_PID_GRACE_SECONDS="${DOCKER_CLEANUP_LOCK_MISSING_PID_GRACE_SECONDS:-15}"
DOCKER_CLEANUP_INSPECT_TIMEOUT_SECONDS="${DOCKER_CLEANUP_INSPECT_TIMEOUT_SECONDS:-10}"
DOCKER_CLEANUP_COMMAND_TIMEOUT_SECONDS="${DOCKER_CLEANUP_COMMAND_TIMEOUT_SECONDS:-300}"

# Deploy-artifact directories to protect from, colon-separated. Every product
# repo that pins recent release images via deploy/artifacts/images-*.env
# should list its checkout(s) here (e.g. each CI runner's working copy), or
# the janitor has no way to know a tag is a recent rollback target once its
# container has been replaced.
DOCKER_CLEANUP_ARTIFACT_DIRS="${DOCKER_CLEANUP_ARTIFACT_DIRS:-}"
# Image repositories eligible for reclamation, space-separated glob patterns.
# Deliberately broad: every sibling product's per-SHA api/worker tags
# accumulate on the same daemon, and protection comes from what is actually
# referenced on the host (see protect_referenced_container_images) rather than
# from an allowlist of names that silently drifts.
DOCKER_CLEANUP_IMAGE_PATTERNS="${DOCKER_CLEANUP_IMAGE_PATTERNS:-*tx-agent-kit*/api *tx-agent-kit*/worker *octospark*/api *octospark*/worker *trace-learn*/api *trace-learn*/worker}"

if ! [[ "$KEEP_DEPLOY_ARTIFACTS" =~ ^[0-9]+$ ]]; then
  echo "KEEP_DEPLOY_ARTIFACTS must be an integer, got '$KEEP_DEPLOY_ARTIFACTS'."
  exit 1
fi

if [[ "$DRY_RUN" != "0" && "$DRY_RUN" != "1" ]]; then
  echo "DRY_RUN must be 0 or 1, got '$DRY_RUN'."
  exit 1
fi

DOCKER_CLEANUP_DISABLE_FILE="${DOCKER_CLEANUP_DISABLE_FILE:-}"
if [[ -n "$DOCKER_CLEANUP_DISABLE_FILE" && -e "$DOCKER_CLEANUP_DISABLE_FILE" ]]; then
  echo "Disabled by $DOCKER_CLEANUP_DISABLE_FILE - skipping this run."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "docker and python3 are required"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Cannot connect to the Docker daemon. Ensure Docker is running."
  exit 1
fi

PROTECTED_IMAGES="$(mktemp -t host-docker-cleanup-protected.XXXXXX)"
CANDIDATE_IMAGES="$(mktemp -t host-docker-cleanup-candidates.XXXXXX)"
SORTED_CANDIDATES="$(mktemp -t host-docker-cleanup-sorted.XXXXXX)"
LOCK_ACQUIRED=0

cleanup() {
  rm -f "$PROTECTED_IMAGES" "$CANDIDATE_IMAGES" "$SORTED_CANDIDATES"
  if [[ "$LOCK_ACQUIRED" == "1" ]]; then
    lock_release "$LOCK_DIR"
  fi
}
trap cleanup EXIT

if ! lock_acquire "$LOCK_DIR" "$LOCK_TIMEOUT_SECONDS" "$LOCK_MISSING_PID_GRACE_SECONDS"; then
  echo "Another Docker cleanup is already in progress."
  exit 1
fi
LOCK_ACQUIRED=1

add_protected_image() {
  local image_ref="$1"
  local image_id=""

  if [[ -z "$image_ref" || "$image_ref" == "<none>" ]]; then
    return 0
  fi

  printf '%s\n' "$image_ref" >> "$PROTECTED_IMAGES"
  image_id="$(run_with_timeout "$DOCKER_CLEANUP_INSPECT_TIMEOUT_SECONDS" \
    docker image inspect "$image_ref" --format '{{.Id}}' 2>/dev/null || true)"
  if [[ -n "$image_id" ]]; then
    printf '%s\n' "$image_id" >> "$PROTECTED_IMAGES"
  fi
}

is_protected_image() {
  local image_ref="$1"
  local image_id="${2:-}"

  if grep -Fxq "$image_ref" "$PROTECTED_IMAGES"; then
    return 0
  fi

  if [[ -n "$image_id" ]] && grep -Fxq "$image_id" "$PROTECTED_IMAGES"; then
    return 0
  fi

  return 1
}

run_or_print() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi

  run_with_timeout "$DOCKER_CLEANUP_COMMAND_TIMEOUT_SECONDS" "$@"
}

# Protect every image referenced by a container that exists on this host, in
# any compose project, running or stopped.
#
# This is derived from the daemon's own container list, not from an allowlist
# of compose project names, so it cannot drift out of sync with how any given
# product names its stacks (a prior allowlist-based version of this script
# named "octospark-staging"/"octospark-prod" while the live stacks ran as
# "octospark-staging-live"/"octospark-prod-live" - it matched zero containers
# and protected nothing; every release survived only because Docker refuses to
# remove an image with a RUNNING container attached, which does not cover a
# stopped-but-current container, exactly the state a release leaves behind
# mid-rollout).
protect_referenced_container_images() {
  local container_ids=""
  local container_id=""
  local image_ref=""
  local image_id=""

  if ! container_ids="$(run_with_timeout "$DOCKER_CLEANUP_INSPECT_TIMEOUT_SECONDS" docker ps -a --format '{{.ID}}' 2>/dev/null)"; then
    echo "Cannot inventory container references; refusing Docker cleanup."
    return 1
  fi

  while IFS= read -r container_id; do
    if [[ -z "$container_id" ]]; then
      continue
    fi

    if ! image_ref="$(run_with_timeout "$DOCKER_CLEANUP_INSPECT_TIMEOUT_SECONDS" \
      docker inspect "$container_id" --format '{{.Config.Image}}' 2>/dev/null)" || \
       ! image_id="$(run_with_timeout "$DOCKER_CLEANUP_INSPECT_TIMEOUT_SECONDS" \
      docker inspect "$container_id" --format '{{.Image}}' 2>/dev/null)" || \
       [[ -z "$image_ref" || -z "$image_id" ]]; then
      echo "Cannot inspect container references; refusing Docker cleanup."
      return 1
    fi
    add_protected_image "$image_ref"
    if [[ -n "$image_id" ]]; then
      printf '%s\n' "$image_id" >> "$PROTECTED_IMAGES"
    fi
  done <<< "$container_ids"
}

artifact_dirs() {
  if [[ -n "$DOCKER_CLEANUP_ARTIFACT_DIRS" ]]; then
    printf '%s\n' "$DOCKER_CLEANUP_ARTIFACT_DIRS" | tr ':' '\n'
  fi
}

protect_artifact_images() {
  local artifact_dir=""
  local artifact_file=""
  local assignment=""
  local image_ref=""

  if (( KEEP_DEPLOY_ARTIFACTS == 0 )); then
    return 0
  fi

  while IFS= read -r artifact_dir; do
    if [[ -z "$artifact_dir" || ! -d "$artifact_dir" ]]; then
      continue
    fi

    while IFS= read -r artifact_file; do
      if [[ -z "$artifact_file" ]]; then
        continue
      fi

      while IFS= read -r assignment; do
        image_ref="${assignment#*=}"
        add_protected_image "$image_ref"
      done < <(load_image_artifact "$artifact_file" 2>/dev/null || true)
    done < <(ls -t "$artifact_dir"/images-*.env 2>/dev/null | head -n "$KEEP_DEPLOY_ARTIFACTS" || true)
  done < <(artifact_dirs)
}

# Minimal KEY=VALUE reader for a deploy/artifacts/images-*.env file - each line
# is expected to look like `API_IMAGE=repo/api:sha`. Intentionally permissive
# (no product-specific parsing) since this script has no product repo checkout
# to defer to.
load_image_artifact() {
  local artifact_file="$1"
  grep -E '^[A-Z_]+=.+$' "$artifact_file" 2>/dev/null || true
}

is_reclaimable_repository() {
  local repository="$1"
  local pattern=""

  for pattern in $DOCKER_CLEANUP_IMAGE_PATTERNS; do
    # shellcheck disable=SC2254  # patterns are globs on purpose
    case "$repository" in
      $pattern) return 0 ;;
    esac
  done

  return 1
}

image_is_old_enough() {
  local metadata=""
  if ! metadata="$(run_with_timeout "$DOCKER_CLEANUP_INSPECT_TIMEOUT_SECONDS" docker image inspect "$1" 2>/dev/null)"; then
    return 1
  fi
  printf '%s\n' "$metadata" | python3 "$SCRIPT_DIR/lib/image-age.py" --min-age-hours "$IMAGE_MIN_AGE_HOURS"
}

collect_candidate_images() {
  local repository=""
  local tag=""
  local digest=""
  local image_id=""
  local image_ref=""

  while IFS=$'\t' read -r repository tag digest image_id; do
    if [[ -z "$repository" || "$repository" == "<none>" ]]; then
      continue
    fi

    if ! is_reclaimable_repository "$repository"; then
      continue
    fi

    if [[ -n "$tag" && "$tag" != "<none>" ]]; then
      image_ref="${repository}:${tag}"
    elif [[ "$digest" == sha256:* ]]; then
      image_ref="${repository}@${digest}"
    else
      continue
    fi
    if ! is_protected_image "$image_ref" "$image_id" && image_is_old_enough "$image_ref"; then
      printf '%s\n' "$image_ref" >> "$CANDIDATE_IMAGES"
    fi
  done < <(docker image ls --digests --no-trunc --format '{{.Repository}}\t{{.Tag}}\t{{.Digest}}\t{{.ID}}')
}

echo "Collecting images referenced by containers on this host..."
protect_referenced_container_images
protect_artifact_images
sort -u "$PROTECTED_IMAGES" -o "$PROTECTED_IMAGES"
echo "Protected $(grep -c . "$PROTECTED_IMAGES" || true) image refs/ids."

echo "Collecting unprotected old image references..."
collect_candidate_images
sort -u "$CANDIDATE_IMAGES" -o "$SORTED_CANDIDATES"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry run enabled. No Docker state will be changed."
fi

if [[ -s "$SORTED_CANDIDATES" ]]; then
  echo "Removing unprotected old release image references:"
  sed 's/^/  /' "$SORTED_CANDIDATES"
  while IFS= read -r image_ref; do
    # A pull/retag after discovery makes the tag recent again. Refresh age at
    # mutation time rather than deleting newly reused cached content.
    if ! image_is_old_enough "$image_ref"; then
      echo "Retaining recently tagged or unverified image: $image_ref"
      continue
    fi
    if ! run_or_print docker image rm "$image_ref"; then
      echo "Warning: failed to remove image tag ${image_ref}; continuing cleanup."
    fi
  done < "$SORTED_CANDIDATES"
else
  echo "No unprotected old release image references found."
fi

if [[ "$PRUNE_STOPPED_CONTAINERS" == "1" ]]; then
  echo "Pruning stopped containers (explicitly enabled)..."
  run_or_print docker container prune -f
else
  echo "Preserving stopped containers."
fi

echo "Pruning unused networks..."
run_or_print docker network prune -f

echo "Pruning dangling images..."
run_or_print docker image prune -f

echo "Pruning BuildKit cache older than ${BUILDER_CACHE_UNTIL}, keeping ${BUILDER_KEEP_STORAGE}..."
STORAGE_FLAG="$(builder_storage_flag docker)"
run_or_print docker buildx prune --builder "$BUILDER_NAME" -af --filter "until=${BUILDER_CACHE_UNTIL}" "$STORAGE_FLAG" "$BUILDER_KEEP_STORAGE"

echo "Docker cleanup complete. Docker volumes were not pruned."
