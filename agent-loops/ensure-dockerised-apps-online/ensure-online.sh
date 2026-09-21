#!/usr/bin/env bash
# Keep every Docker Compose app in config/docker-apps.json online, idempotently.
#
# WHY THIS EXISTS
# On 2026-07-26 the Mac Studio rebooted and the whole Trace Learn stack - prod AND
# staging - stayed down for ~20 HOURS. api.tracelearn.app served 502 the entire time
# while tracelearn.app kept returning 200, because the frontend is Cloudflare Pages and
# cloudflared runs as its own launchd agent. Nothing looked broken from outside.
#
# Docker Desktop on macOS starts at user LOGIN, not at boot, and a restart policy is
# meaningless while the daemon itself is down. Some stacks (tx-agent-kit) also run with
# restart policy `no`, so nothing but this loop ever brings them back.
#
# DELIBERATELY NOT A DEPLOY. It only `docker start`s containers that already exist:
# no build, no registry pull, no secrets, no migrations. If an app has never been
# deployed on this host there is nothing to start and that is not an error.
#
# ADDING AN APP: append it to config/docker-apps.json. Nothing here needs editing.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="${ENSURE_DOCKER_APPS_STATE_DIR:-$ROOT_DIR/state/ensure-dockerised-apps-online}"
DISABLE_FLAG="${ENSURE_DOCKER_APPS_DISABLE_FILE:-$STATE_DIR/disabled}"
DOCKER_WAIT_SECONDS="${ENSURE_DOCKER_APPS_DOCKER_WAIT_SECONDS:-300}"
ONLY_APP=""
DRY_RUN=0

# shellcheck source=../../lib/docker-apps.sh
source "$ROOT_DIR/lib/docker-apps.sh"

usage() {
  cat <<'HELP'
Usage:
  ensure-online.sh                 start any stopped container of every enabled app
  ensure-online.sh --dry-run       report what would be started, change nothing
  ensure-online.sh --app ID        limit to one app id from config/docker-apps.json
  ensure-online.sh --status        show container state per app and exit

Maintenance opt-out (checked on every run):
  touch <state>/disabled           hold EVERY app down
  touch <state>/disabled.<app-id>  hold one app down
HELP
}

MODE="run"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --status) MODE="status"; shift ;;
    --app) ONLY_APP="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$STATE_DIR"

log() {
  printf '%s ensure-dockerised-apps-online: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

wait_for_docker() {
  local waited=0
  until docker info >/dev/null 2>&1; do
    if (( waited >= DOCKER_WAIT_SECONDS )); then
      log "docker daemon still unavailable after ${DOCKER_WAIT_SECONDS}s - giving up this run"
      return 1
    fi
    sleep 5
    waited=$(( waited + 5 ))
  done
  if (( waited > 0 )); then
    log "docker daemon became available after ${waited}s"
  fi
}

show_status() {
  local app_id project service delay name state
  while IFS=$'\t' read -r app_id project; do
    [ -z "$ONLY_APP" ] || [ "$ONLY_APP" = "$app_id" ] || continue
    if [ -f "$STATE_DIR/disabled.$app_id" ]; then
      echo "[$app_id] ($project) DISABLED via $STATE_DIR/disabled.$app_id"
    else
      echo "[$app_id] ($project)"
    fi
    while IFS=$'\t' read -r service delay; do
      local found=0
      while IFS=$'\t' read -r name state; do
        [ -n "$name" ] || continue
        found=1
        printf '  %-18s %-40s %s\n' "$service" "$name" "$state"
      done < <(docker_apps_containers "$project" "$service")
      if (( found == 0 )); then
        printf '  %-18s %-40s %s\n' "$service" "-" "absent (never deployed here)"
      fi
    done < <(docker_apps_query start-order "$app_id")
  done < <(docker_apps_query list)
}

# MAINTENANCE OPT-OUT. Without this, an operator who deliberately stops prod (to take a
# backup, to hold traffic during an incident) would find it silently restarted under
# them within the interval. Same hazard `restart: always` carries; here it is escapable.
if [[ -f "$DISABLE_FLAG" ]]; then
  log "disabled by $DISABLE_FLAG - leaving all apps alone"
  exit 0
fi

if [ "$MODE" = "status" ]; then
  docker info >/dev/null 2>&1 || { echo "docker daemon unavailable" >&2; exit 1; }
  show_status
  exit 0
fi

wait_for_docker || exit 0

started_any=0
failed_any=0
apps_seen=0

while IFS=$'\t' read -r app_id project; do
  [ -z "$ONLY_APP" ] || [ "$ONLY_APP" = "$app_id" ] || continue
  apps_seen=$(( apps_seen + 1 ))

  if [[ -f "$STATE_DIR/disabled.$app_id" ]]; then
    log "[$app_id] disabled by $STATE_DIR/disabled.$app_id - skipping"
    continue
  fi

  # Dependency order comes from the config: databases and proxies first, then the
  # services that need them.
  while IFS=$'\t' read -r service delay; do
    while IFS=$'\t' read -r name state; do
      [ -n "$name" ] || continue
      [ "$state" = "running" ] && continue

      if (( DRY_RUN )); then
        log "[$app_id] would start $name (is $state)"
        started_any=1
        continue
      fi

      if docker start "$name" >/dev/null 2>&1; then
        log "[$app_id] started $name (was $state)"
        started_any=1
        # Let a dependency accept connections before its dependents try to use it.
        # Written as a full `if`, NOT `[[ ... ]] && sleep`: as the last statement of a
        # then-block a false condition returns 1, and under `set -e` that would exit the
        # whole script and silently abort bring-up after the first container.
        if [ "${delay:-0}" != "0" ]; then
          sleep "$delay"
        fi
      else
        log "[$app_id] FAILED to start $name (was $state)"
        failed_any=1
      fi
    done < <(docker_apps_containers "$project" "$service")
  done < <(docker_apps_query start-order "$app_id")
done < <(docker_apps_query list)

if [ -n "$ONLY_APP" ] && (( apps_seen == 0 )); then
  echo "No enabled app with id '$ONLY_APP' in $DOCKER_APPS_CONFIG" >&2
  exit 2
fi

if (( started_any == 0 && failed_any == 0 )); then
  log "nothing to do - every existing container of ${apps_seen} app(s) is already running"
fi

# Non-zero only on a real failure, so a launchd run that had nothing to do stays quiet
# while a genuinely stuck container is visible in the job log.
exit "$failed_any"
