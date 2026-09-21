#!/usr/bin/env bash
# Shared helpers for loops that operate on the Docker Compose apps listed in
# config/docker-apps.json. Source this file; do not execute it.
#
#   docker_apps_query list                      -> ID<TAB>COMPOSE_PROJECT
#   docker_apps_query start-order ID            -> SERVICE<TAB>DELAY
#   docker_apps_query temporal ID               -> ENABLED<TAB>CHECK_FROM<TAB>RESTART_CSV<TAB>KICKSTART_CSV
#   docker_apps_default KEY                     -> value from the defaults block
#   docker_apps_containers PROJECT SERVICE      -> NAME<TAB>STATE for every matching container
#   docker_apps_running_container PROJECT SERVICE -> first running container name, or exit 1
#   docker_apps_container_env NAME VAR          -> value of VAR inside the container's env, or empty

_docker_apps_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_APPS_ROOT="$(cd "$_docker_apps_lib_dir/.." && pwd)"
DOCKER_APPS_CONFIG="${DOCKER_APPS_CONFIG:-$DOCKER_APPS_ROOT/config/docker-apps.json}"
export DOCKER_APPS_CONFIG

docker_apps_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

docker_apps_query() {
  local py=""
  py="$(docker_apps_python)" || {
    echo "docker-apps: python3 is required to read $DOCKER_APPS_CONFIG" >&2
    return 1
  }
  "$py" "$_docker_apps_lib_dir/docker-apps.py" "$@"
}

docker_apps_default() {
  local key="$1"
  docker_apps_query defaults | awk -F'\t' -v k="$key" '$1 == k { print $2; exit }'
}

# Compose labels, not names: `docker compose` names containers PROJECT-SERVICE-N but a
# scaled or renamed service still carries the same labels.
docker_apps_containers() {
  local project="$1"
  local service="$2"
  docker ps -a \
    --filter "label=com.docker.compose.project=${project}" \
    --filter "label=com.docker.compose.service=${service}" \
    --format '{{.Names}}\t{{.State}}'
}

docker_apps_running_container() {
  local project="$1"
  local service="$2"
  local name=""
  name="$(docker ps \
    --filter "label=com.docker.compose.project=${project}" \
    --filter "label=com.docker.compose.service=${service}" \
    --format '{{.Names}}' | head -n 1)"
  [ -n "$name" ] || return 1
  printf '%s\n' "$name"
}

docker_apps_container_env() {
  local container="$1"
  local var="$2"
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null \
    | awk -F= -v v="$var" '$1 == v { sub(/^[^=]*=/, ""); print; exit }'
}
