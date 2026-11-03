#!/usr/bin/env bash
# Weekly, age-based cleanup of the shared CI caches on an A Series runner host.
#
# The octospark and smart-cub-learning workflows keep their pnpm store, Turbo
# cache and local BuildKit caches under /home/ci-runner/.cache/<repo>-ci and
# never prune them inside a job. Each repository owns the cleanup rules in
# scripts/deploy/ci/host-cache-cleanup.sh; this wrapper runs that script from
# the newest runner workspace checkout of each repository, then prunes the
# repository's docker-container BuildKit builder through buildctl, because the
# builders are registered in job-scoped DOCKER_CONFIG directories and are not
# visible to `docker buildx` from here.
#
# Usage: cache-cleanup.sh [--dry-run]
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: cache-cleanup.sh [--dry-run]" >&2
  exit 2
fi

RUNNER_HOME="${RUNNER_HOME:-/home/ci-runner}"
MAX_AGE_DAYS="${CI_CACHE_MAX_AGE_DAYS:-14}"
# buildctl counts --keep-storage in megabytes.
KEEP_STORAGE_MB="${CI_BUILDKIT_KEEP_STORAGE_MB:-40000}"

declare -A BUILDERS=(
  [octospark]=octospark-cache-builder
  [smart-cub-learning]=trace-learn-cache-builder
)

status=0
for repo in octospark smart-cub-learning; do
  echo "== $repo"
  script="$(ls -t "$RUNNER_HOME"/actions-runner-*/_work*/"$repo"/"$repo"/scripts/deploy/ci/host-cache-cleanup.sh 2>/dev/null | head -n 1 || true)"
  if [[ -z "$script" ]]; then
    echo "no runner workspace checkout of $repo on this host; skipping its cache root"
  else
    args=()
    [[ "$DRY_RUN" == 1 ]] && args+=(--dry-run)
    case "$repo" in
      octospark) repo_env=(OCTOSPARK_CACHE_MAX_AGE_DAYS="$MAX_AGE_DAYS") ;;
      smart-cub-learning) repo_env=(); args+=(--retention-days "$MAX_AGE_DAYS") ;;
    esac
    echo "running $script ${args[*]}"
    if ! env -i HOME="$RUNNER_HOME" PATH=/usr/local/bin:/usr/bin:/bin "${repo_env[@]}" bash "$script" "${args[@]}"; then
      echo "$repo cache cleanup failed" >&2
      status=1
    fi
  fi

  container="buildx_buildkit_${BUILDERS[$repo]}0"
  if ! docker container inspect "$container" >/dev/null 2>&1; then
    echo "builder container $container not present; skipping builder prune"
    continue
  fi
  prune=(docker exec "$container" buildctl prune --keep-duration "$((MAX_AGE_DAYS * 24))h" --keep-storage "$KEEP_STORAGE_MB")
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "would run: ${prune[*]}"
  elif ! "${prune[@]}"; then
    echo "$container prune failed" >&2
    status=1
  fi
done
exit "$status"
