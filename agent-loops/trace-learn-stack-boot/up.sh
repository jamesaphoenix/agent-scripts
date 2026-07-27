#!/usr/bin/env bash
# Bring the Trace Learn compose stacks back up, idempotently.
#
# WHY THIS EXISTS
# On 2026-07-26 the Mac Studio rebooted and the whole Trace Learn stack - prod AND
# staging - stayed down for ~20 HOURS. api.tracelearn.app served 502 the entire time
# while tracelearn.app kept returning 200, because the frontend is Cloudflare Pages and
# cloudflared runs as its own launchd agent. The app loaded and then failed every
# request, so nothing looked broken from outside.
#
# Docker Desktop on macOS starts at user LOGIN, not at boot, and the containers had
# exited with graceful-stop codes (0/143/137) - which `restart: unless-stopped`
# deliberately does not undo. The compose files now use `restart: always`, but that
# cannot help while the Docker daemon itself is not running. This closes that gap.
#
# DELIBERATELY NOT A DEPLOY. It only `docker start`s containers that already exist:
# no build, no registry pull, no secrets, no migrations. If a stack has never been
# deployed on this host there is nothing to start and that is not an error.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DISABLE_FLAG="${TRACE_LEARN_STACK_BOOT_DISABLE_FILE:-$ROOT_DIR/state/trace-learn-stack-boot/disabled}"
DOCKER_WAIT_SECONDS="${TRACE_LEARN_STACK_BOOT_DOCKER_WAIT_SECONDS:-300}"

log() {
  printf '%s trace-learn-stack-boot: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

# MAINTENANCE OPT-OUT. Without this, an operator who deliberately stops prod (to take a
# backup, to hold traffic during an incident) would find it silently restarted under
# them within the interval. Same hazard `restart: always` carries; here it is escapable:
#   touch  <state>/trace-learn-stack-boot/disabled   to hold everything down
#   rm     that file                                  to resume automatic bring-up
if [[ -f "$DISABLE_FLAG" ]]; then
  log "disabled by $DISABLE_FLAG - leaving all stacks alone"
  exit 0
fi

# Docker Desktop starts at login and takes time to become usable. Waiting beats failing
# and hoping the next interval finds it ready.
waited=0
until docker info >/dev/null 2>&1; do
  if (( waited >= DOCKER_WAIT_SECONDS )); then
    log "docker daemon still unavailable after ${DOCKER_WAIT_SECONDS}s - giving up this run"
    exit 0
  fi
  sleep 5
  waited=$(( waited + 5 ))
done
if (( waited > 0 )); then
  log "docker daemon became available after ${waited}s"
fi

# Dependency order matters: api and worker need the CloudSQL proxy to reach the
# database, and both emit through the otel collector.
SERVICES_IN_ORDER=(cloudsql-proxy otel-collector otel-healthcheck api worker)
started_any=0
failed_any=0

for env_name in prod staging; do
  for service in "${SERVICES_IN_ORDER[@]}"; do
    container="trace-learn-${env_name}-${service}-1"

    if ! docker container inspect "$container" >/dev/null 2>&1; then
      continue
    fi

    state="$(docker container inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo unknown)"
    if [[ "$state" == "running" ]]; then
      continue
    fi

    if docker start "$container" >/dev/null 2>&1; then
      log "started $container (was $state)"
      started_any=1
      # Let the proxy accept connections before api/worker try to use it. Written as a
      # full `if`, NOT `[[ ... ]] && sleep`: as the last statement in this then-block a
      # false condition returns 1, and under `set -e` that exits the whole script -
      # silently aborting bring-up after the first non-proxy container.
      if [[ "$service" == "cloudsql-proxy" ]]; then
        sleep 8
      fi
    else
      log "FAILED to start $container (was $state)"
      failed_any=1
    fi
  done
done

if (( started_any == 0 && failed_any == 0 )); then
  log "nothing to do - all existing Trace Learn containers already running"
fi

# Non-zero only on a real failure, so a launchd run that had nothing to do stays quiet
# while a genuinely stuck container is visible in the job log.
exit "$failed_any"
