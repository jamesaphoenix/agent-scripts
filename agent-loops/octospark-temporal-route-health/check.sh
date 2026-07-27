#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="${OCTOSPARK_TEMPORAL_ROUTE_STATE_DIR:-$ROOT_DIR/state/octospark-temporal-route-health}"
mkdir -p "$STATE_DIR"

json_escape() {
  sed \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e 's/	/\\t/g'
}

write_status() {
  local status="$1"
  local detail="$2"
  local now
  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  {
    printf '{\n'
    printf '  "status": "%s",\n' "$(printf '%s' "$status" | json_escape)"
    printf '  "detail": "%s",\n' "$(printf '%s' "$detail" | json_escape)"
    printf '  "checkedAt": "%s"\n' "$now"
    printf '}\n'
  } >"$STATE_DIR/last-status.json"
}

parse_temporal_address() {
  local address="$1"
  local host="${address%:*}"
  local port="${address##*:}"

  if [[ -z "$host" || -z "$port" || "$host" == "$address" || ! "$port" =~ ^[0-9]+$ ]]; then
    echo "Invalid Temporal address '${address}'. Expected host:port." >&2
    return 1
  fi

  printf '%s\t%s\n' "$host" "$port"
}

is_ipv4() {
  [[ "$1" =~ ^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$ ]]
}

is_tailscale_ipv4() {
  local host="$1"
  local a="" b="" c="" d=""

  IFS=. read -r a b c d <<<"$host"
  [[ "$a" == "100" && "$b" =~ ^[0-9]+$ && "$b" -ge 64 && "$b" -le 127 && "$c" =~ ^[0-9]+$ && "$d" =~ ^[0-9]+$ ]]
}

resolve_ipv4() {
  local host="$1"
  if is_ipv4 "$host"; then
    printf '%s\n' "$host"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$host" <<'PY'
import socket
import sys

host = sys.argv[1]
try:
    for info in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM):
        print(info[4][0])
        raise SystemExit(0)
except OSError:
    pass
raise SystemExit(1)
PY
    return
  fi

  return 1
}

tailscale_bin() {
  if command -v tailscale >/dev/null 2>&1; then
    command -v tailscale
    return 0
  fi

  if [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
    printf '%s\n' /Applications/Tailscale.app/Contents/MacOS/Tailscale
    return 0
  fi

  return 1
}

load_temporal_address() {
  local env_name="${OCTOSPARK_ENV:-staging}"
  local address="${TEMPORAL_ADDRESS:-}"
  local nas_ip=""

  if [ -z "$address" ] && [ -f "$ROOT_DIR/lib/op-service-account-env.sh" ]; then
    # shellcheck source=../../lib/op-service-account-env.sh
    source "$ROOT_DIR/lib/op-service-account-env.sh"
    agent_scripts_export_op_service_account || true
  fi

  # `run_op`, not bare `op`. This function runs from a LAUNCHD job, which is the exact
  # session where op wedges indefinitely: its credential path open()s the 1Password desktop
  # app's TCC-protected group container, macOS tries to raise a consent prompt nothing can
  # answer, and the call never returns (traced at 17.34s and still blocked at SIGKILL).
  # `|| true` made that strictly worse - it cannot fail, only hang forever, so a wedge here
  # silently stops this health check rather than reporting anything.
  if [ -z "$address" ] && [ -f "$ROOT_DIR/lib/op-cli.sh" ]; then
    # shellcheck source=../../lib/op-cli.sh
    source "$ROOT_DIR/lib/op-cli.sh"
  fi

  if [ -z "$address" ] && [ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ] && command -v op >/dev/null 2>&1; then
    address="$(run_op read "op://octospark-services/${env_name}/TEMPORAL_ADDRESS" 2>/dev/null || true)"
    if [ -z "$address" ]; then
      nas_ip="$(run_op read "op://octospark-services/${env_name}/NAS_TAILSCALE_IP" 2>/dev/null || true)"
      if [ -n "$nas_ip" ]; then
        address="${nas_ip}:7233"
      fi
    fi
  fi

  printf '%s\n' "${address:-100.123.72.113:7233}"
}

route_check() {
  local route_target="$1"
  local route_output=""

  if ! command -v route >/dev/null 2>&1; then
    echo "route command is required for Temporal route checks." >&2
    return 1
  fi

  route_output="$(route -n get "$route_target" 2>&1)" || {
    printf '%s\n' "$route_output"
    return 1
  }
  printf '%s\n' "$route_output" | tee "$STATE_DIR/last-route.txt"

  if is_tailscale_ipv4 "$route_target" && ! grep -Eq 'interface:[[:space:]]+utun[0-9]+' <<<"$route_output"; then
    echo "Temporal route check failed: ${route_target} is not routed through a utun interface." >&2
    return 1
  fi
}

tcp_check_host() {
  local host="$1"
  local port="$2"

  echo "Checking host TCP to Temporal at ${host}:${port}..."
  if [[ "$(uname -s)" == "Darwin" ]] && command -v nc >/dev/null 2>&1; then
    nc -z -G 5 -w 5 "$host" "$port"
    return
  fi

  TEMPORAL_CHECK_HOST="$host" TEMPORAL_CHECK_PORT="$port" node -e '
const net = require("node:net");
const host = process.env.TEMPORAL_CHECK_HOST;
const port = Number(process.env.TEMPORAL_CHECK_PORT);
const socket = net.createConnection({ host, port, timeout: 5000 }, () => {
  socket.destroy();
  process.exit(0);
});
socket.on("timeout", () => {
  socket.destroy();
  console.error(`Timed out connecting to ${host}:${port}`);
  process.exit(1);
});
socket.on("error", (error) => {
  console.error(`Failed connecting to ${host}:${port}: ${error.message}`);
  process.exit(1);
});
'
}

compose_container_id() {
  local service="$1"
  local project="${OCTOSPARK_COMPOSE_PROJECT_NAME:-octospark-staging-live}"
  local explicit_var=""
  local explicit=""
  local candidate=""

  case "$service" in
    api) explicit_var="OCTOSPARK_API_CONTAINER" ;;
    worker) explicit_var="OCTOSPARK_WORKER_CONTAINER" ;;
    *) explicit_var="" ;;
  esac

  if [ -n "$explicit_var" ]; then
    explicit="${!explicit_var:-}"
  fi

  if [ -n "$explicit" ]; then
    printf '%s\n' "$explicit"
    return 0
  fi

  candidate="$(docker ps \
    --filter "label=com.docker.compose.project=${project}" \
    --filter "label=com.docker.compose.service=${service}" \
    --format '{{.ID}}' | head -n 1)"
  if [ -n "$candidate" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  for candidate in "${project}-${service}-1" "octospark-staging-${service}-1"; do
    if docker ps --format '{{.Names}}' | grep -Fxq "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

tcp_check_docker() {
  local host="$1"
  local port="$2"
  local container=""

  container="$(compose_container_id api)" || {
    echo "Could not find the Octospark API container for Docker Temporal TCP check." >&2
    return 1
  }

  echo "Checking Docker TCP to Temporal from API container ${container} at ${host}:${port}..."
  docker exec \
    -e TEMPORAL_CHECK_HOST="$host" \
    -e TEMPORAL_CHECK_PORT="$port" \
    "$container" \
    node -e '
const net = require("node:net");
const host = process.env.TEMPORAL_CHECK_HOST;
const port = Number(process.env.TEMPORAL_CHECK_PORT);
const socket = net.createConnection({ host, port, timeout: 5000 }, () => {
  socket.destroy();
  process.exit(0);
});
socket.on("timeout", () => {
  socket.destroy();
  console.error(`Timed out connecting to ${host}:${port}`);
  process.exit(1);
});
socket.on("error", (error) => {
  console.error(`Failed connecting to ${host}:${port}: ${error.message}`);
  process.exit(1);
});
'
}

run_checks() {
  local host="$1"
  local port="$2"
  local route_target="$3"

  echo "Checking route to Temporal target ${route_target}..."
  route_check "$route_target"
  tcp_check_host "$host" "$port"
  tcp_check_docker "$host" "$port"
}

repair_tailscale_route() {
  local bin=""
  bin="$(tailscale_bin)" || {
    echo "Tailscale CLI not found; cannot repair route." >&2
    return 1
  }

  echo "Repairing Tailscale route with ${bin} down/up..."
  "$bin" down || true
  sleep "${OCTOSPARK_TAILSCALE_REPAIR_SLEEP_SECONDS:-3}"
  "$bin" up --timeout="${OCTOSPARK_TAILSCALE_UP_TIMEOUT:-20s}"
}

restart_worker_after_repair() {
  if [ "${OCTOSPARK_RESTART_WORKER_ON_REPAIR:-1}" != "1" ]; then
    return 0
  fi

  local worker=""
  worker="$(compose_container_id worker)" || {
    echo "Could not find the Octospark worker container to restart after Tailscale repair."
    return 0
  }

  echo "Restarting worker container ${worker} after Temporal route repair..."
  docker restart "$worker" >/dev/null
}

main() {
  local address=""
  local parsed=""
  local host=""
  local port=""
  local route_target=""

  address="$(load_temporal_address)"
  parsed="$(parse_temporal_address "$address")"
  IFS=$'\t' read -r host port <<<"$parsed"
  route_target="$(resolve_ipv4 "$host" 2>/dev/null || printf '%s\n' "$host")"

  if run_checks "$host" "$port" "$route_target"; then
    write_status "ok" "Temporal route and TCP checks passed for ${host}:${port}"
    return 0
  fi

  echo "Temporal route or TCP check failed for ${host}:${port}." >&2
  if [ "${OCTOSPARK_TEMPORAL_ROUTE_REPAIR:-1}" != "1" ]; then
    write_status "failed" "Temporal route or TCP checks failed and repair is disabled"
    return 1
  fi

  repair_tailscale_route
  sleep "${OCTOSPARK_POST_REPAIR_CHECK_DELAY_SECONDS:-5}"

  if run_checks "$host" "$port" "$route_target"; then
    restart_worker_after_repair
    write_status "repaired" "Temporal route and TCP checks passed after Tailscale repair"
    return 0
  fi

  write_status "failed" "Temporal route or TCP checks still failed after Tailscale repair"
  return 1
}

main "$@"
