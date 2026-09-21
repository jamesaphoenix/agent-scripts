#!/usr/bin/env bash
# Temporal worker Tailscale health: verify every Temporal-backed app in
# config/docker-apps.json can reach the Temporal server over Tailscale, and repair the
# route when it goes stale.
#
# Every Octospark and Trace Learn worker on this host talks to the Hetzner Temporal server
# over Tailscale. Tailscale on macOS periodically leaves a stale route: `tailscale ping`
# still succeeds but a normal TCP connection does not, and every worker silently stops
# polling its task queue. This loop checks, in order:
#
#   1. the macOS routing table sends the Temporal IP through a utun interface;
#   2. a plain TCP connect from the host reaches Temporal;
#   3. a TCP connect from INSIDE each app's `checkFromService` container reaches Temporal,
#      using the TEMPORAL_ADDRESS that container was actually started with.
#
# On any failure it runs `tailscale down` / `tailscale up`, re-checks, and then restarts
# every configured worker and kickstarts every configured launchd runner so they drop
# dead gRPC connections. Repairs are rate-limited so a persistent outage cannot flap the
# tunnel every minute.
#
# ADDING AN APP: give it a `temporal` block in config/docker-apps.json. Nothing here
# needs editing.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="${TEMPORAL_HEALTH_STATE_DIR:-$ROOT_DIR/state/temporal-worker-tailscale-health}"
REPAIR="${TEMPORAL_HEALTH_REPAIR:-1}"
REPAIR_COOLDOWN_SECONDS="${TEMPORAL_HEALTH_REPAIR_COOLDOWN_SECONDS:-600}"
POST_REPAIR_DELAY_SECONDS="${TEMPORAL_HEALTH_POST_REPAIR_DELAY_SECONDS:-5}"
TAILSCALE_UP_TIMEOUT="${TEMPORAL_HEALTH_TAILSCALE_UP_TIMEOUT:-20s}"
# Hard ceiling for one run. No coreutils `timeout` on the Studio, so it is enforced by
# hand below. run-job.sh's lock already stops overlapping ticks.
DEADLINE_SECONDS="${TEMPORAL_HEALTH_DEADLINE_SECONDS:-170}"
TCP_TIMEOUT_SECONDS=5
MODE="check"

# shellcheck source=../../lib/docker-apps.sh
source "$ROOT_DIR/lib/docker-apps.sh"

usage() {
  cat <<'HELP'
Usage:
  check.sh              check every Temporal-backed app; repair Tailscale on failure
  check.sh --no-repair  check only, never touch Tailscale or restart anything
  check.sh --status     print the last recorded status and exit
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-repair) REPAIR=0; shift ;;
    --status) MODE="status"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$STATE_DIR"

if [ "$MODE" = "status" ]; then
  if [ -f "$STATE_DIR/last-status.json" ]; then
    cat "$STATE_DIR/last-status.json"
  else
    echo "no status recorded yet in $STATE_DIR"
  fi
  exit 0
fi

log() {
  printf '%s temporal-worker-tailscale-health: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

json_escape() {
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/	/\\t/g'
}

# Per-app results accumulate here as "id<TAB>container<TAB>address<TAB>result<TAB>detail".
APP_RESULTS=()

write_status() {
  local status="$1"
  local detail="$2"
  local now line id container address result app_detail first=1
  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  {
    printf '{\n'
    printf '  "status": "%s",\n' "$(printf '%s' "$status" | json_escape)"
    printf '  "detail": "%s",\n' "$(printf '%s' "$detail" | json_escape)"
    printf '  "checkedAt": "%s",\n' "$now"
    printf '  "apps": [\n'
    for line in "${APP_RESULTS[@]+"${APP_RESULTS[@]}"}"; do
      IFS=$'\t' read -r id container address result app_detail <<<"$line"
      if (( first )); then first=0; else printf ',\n'; fi
      printf '    {"id": "%s", "container": "%s", "temporalAddress": "%s", "result": "%s", "detail": "%s"}' \
        "$(printf '%s' "$id" | json_escape)" \
        "$(printf '%s' "$container" | json_escape)" \
        "$(printf '%s' "$address" | json_escape)" \
        "$(printf '%s' "$result" | json_escape)" \
        "$(printf '%s' "$app_detail" | json_escape)"
    done
    printf '\n  ]\n}\n'
  } >"$STATE_DIR/last-status.json"
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

is_ipv4() {
  [[ "$1" =~ ^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$ ]]
}

is_tailscale_ipv4() {
  local a="" b="" c="" d=""
  IFS=. read -r a b c d <<<"$1"
  [[ "$a" == "100" && "$b" =~ ^[0-9]+$ && "$b" -ge 64 && "$b" -le 127 && "$c" =~ ^[0-9]+$ && "$d" =~ ^[0-9]+$ ]]
}

resolve_ipv4() {
  local host="$1"
  if is_ipv4 "$host"; then
    printf '%s\n' "$host"
    return 0
  fi
  local py=""
  py="$(docker_apps_python)" || return 1
  "$py" - "$host" <<'PY'
import socket, sys
try:
    for info in socket.getaddrinfo(sys.argv[1], None, socket.AF_INET, socket.SOCK_STREAM):
        print(info[4][0]); raise SystemExit(0)
except OSError:
    pass
raise SystemExit(1)
PY
}

split_address() {
  local address="$1"
  local host="${address%:*}"
  local port="${address##*:}"
  if [[ -z "$host" || -z "$port" || "$host" == "$address" || ! "$port" =~ ^[0-9]+$ ]]; then
    echo "Invalid Temporal address '${address}'. Expected host:port." >&2
    return 1
  fi
  printf '%s\t%s\n' "$host" "$port"
}

route_check() {
  local target="$1"
  local output=""
  output="$(route -n get "$target" 2>&1)" || {
    log "route lookup failed for ${target}: ${output}"
    return 1
  }
  printf '%s\n' "$output" >"$STATE_DIR/last-route.txt"
  if is_tailscale_ipv4 "$target" && ! grep -Eq 'interface:[[:space:]]+utun[0-9]+' <<<"$output"; then
    log "route check failed: ${target} is not routed through a utun interface"
    return 1
  fi
  return 0
}

tcp_check_host() {
  local host="$1" port="$2"
  if nc -z -G "$TCP_TIMEOUT_SECONDS" -w "$TCP_TIMEOUT_SECONDS" "$host" "$port" >/dev/null 2>&1; then
    return 0
  fi
  log "host TCP check failed for ${host}:${port}"
  return 1
}

# TCP connect from inside a container. Prefers node (every Octospark / Trace Learn image
# ships it), then bash's /dev/tcp, then nc. Returns 2 when the image has none of them so
# the caller can record "unknown" instead of a false failure.
tcp_check_container() {
  local container="$1" host="$2" port="$3"
  if docker exec "$container" sh -c 'command -v node >/dev/null 2>&1'; then
    docker exec -e H="$host" -e P="$port" -e T="$(( TCP_TIMEOUT_SECONDS * 1000 ))" "$container" node -e '
const net = require("node:net");
const s = net.createConnection({ host: process.env.H, port: Number(process.env.P), timeout: Number(process.env.T) }, () => { s.destroy(); process.exit(0); });
s.on("timeout", () => { s.destroy(); process.exit(1); });
s.on("error", () => process.exit(1));
' >/dev/null 2>&1
    return $?
  fi
  if docker exec "$container" sh -c 'command -v bash >/dev/null 2>&1'; then
    docker exec "$container" bash -c "exec 3<>/dev/tcp/${host}/${port}" >/dev/null 2>&1
    return $?
  fi
  if docker exec "$container" sh -c 'command -v nc >/dev/null 2>&1'; then
    docker exec "$container" nc -z -w "$TCP_TIMEOUT_SECONDS" "$host" "$port" >/dev/null 2>&1
    return $?
  fi
  return 2
}

# Fills APP_RESULTS and prints the distinct host:port targets seen. Returns 1 if any
# app failed its container-level check.
check_apps() {
  local default_address default_env app_id project enabled check_from restart kick
  local container address host port result detail failed=0
  default_address="$(docker_apps_default temporalAddress)"
  default_env="$(docker_apps_default temporalAddressEnvVar)"
  APP_RESULTS=()

  while IFS=$'\t' read -r app_id project; do
    IFS=$'\t' read -r enabled check_from restart kick < <(docker_apps_query temporal "$app_id")
    [ "$enabled" = "1" ] || continue

    if ! container="$(docker_apps_running_container "$project" "$check_from")"; then
      # Not deployed here, or stopped. Starting it is ensure-dockerised-apps-online's job.
      APP_RESULTS+=("${app_id}"$'\t'"-"$'\t'"-"$'\t'"skipped"$'\t'"no running ${check_from} container for compose project ${project}")
      continue
    fi

    address="$(docker_apps_container_env "$container" "${default_env:-TEMPORAL_ADDRESS}")"
    address="${address:-$default_address}"
    if ! IFS=$'\t' read -r host port < <(split_address "$address"); then
      APP_RESULTS+=("${app_id}"$'\t'"${container}"$'\t'"${address}"$'\t'"failed"$'\t'"unparseable Temporal address")
      failed=1
      continue
    fi
    printf '%s:%s\n' "$host" "$port" >>"$STATE_DIR/.targets.$$"

    set +e
    tcp_check_container "$container" "$host" "$port"
    result=$?
    set -e
    case "$result" in
      0) APP_RESULTS+=("${app_id}"$'\t'"${container}"$'\t'"${address}"$'\t'"ok"$'\t'"container TCP connect succeeded") ;;
      2) APP_RESULTS+=("${app_id}"$'\t'"${container}"$'\t'"${address}"$'\t'"unknown"$'\t'"image has no node, bash or nc to probe with") ;;
      *)
        log "[${app_id}] container ${container} cannot reach Temporal at ${address}"
        APP_RESULTS+=("${app_id}"$'\t'"${container}"$'\t'"${address}"$'\t'"failed"$'\t'"container TCP connect failed")
        failed=1
        ;;
    esac
  done < <(docker_apps_query temporal-apps)

  return "$failed"
}

# Host-level checks against every distinct target the containers use. Falls back to the
# configured default when no container is running so a fully-down host still reports
# whether the tunnel itself is healthy.
check_host() {
  local targets="" target host port ip failed=0
  if [ -s "$STATE_DIR/.targets.$$" ]; then
    targets="$(sort -u "$STATE_DIR/.targets.$$")"
  else
    targets="$(docker_apps_default temporalAddress)"
  fi
  while read -r target; do
    [ -n "$target" ] || continue
    IFS=$'\t' read -r host port < <(split_address "$target") || { failed=1; continue; }
    ip="$(resolve_ipv4 "$host" 2>/dev/null || printf '%s' "$host")"
    route_check "$ip" || failed=1
    tcp_check_host "$host" "$port" || failed=1
  done <<<"$targets"
  return "$failed"
}

run_checks() {
  local host_ok=1 apps_ok=1
  rm -f "$STATE_DIR/.targets.$$"
  check_apps || apps_ok=0
  check_host || host_ok=0
  rm -f "$STATE_DIR/.targets.$$"
  (( host_ok && apps_ok ))
}

repair_allowed() {
  local last=0 now
  now="$(date +%s)"
  if [ -f "$STATE_DIR/last-repair-at" ]; then
    last="$(cat "$STATE_DIR/last-repair-at" 2>/dev/null || echo 0)"
  fi
  if (( now - last < REPAIR_COOLDOWN_SECONDS )); then
    log "repair skipped: last repair was $(( now - last ))s ago (cooldown ${REPAIR_COOLDOWN_SECONDS}s)"
    return 1
  fi
  return 0
}

repair_tailscale_route() {
  local bin=""
  bin="$(tailscale_bin)" || {
    log "Tailscale CLI not found; cannot repair route"
    return 1
  }
  date +%s >"$STATE_DIR/last-repair-at"
  log "repairing Tailscale route with ${bin} down/up"
  "$bin" down || true
  sleep 3
  "$bin" up --timeout="$TAILSCALE_UP_TIMEOUT"
}

# A route flap is host-wide, so every configured worker gets restarted after a repair,
# not only the ones whose probe happened to fail first.
restart_after_repair() {
  local app_id project enabled check_from restart kick service label name uid
  uid="$(id -u)"
  while IFS=$'\t' read -r app_id project; do
    IFS=$'\t' read -r enabled check_from restart kick < <(docker_apps_query temporal "$app_id")
    [ "$enabled" = "1" ] || continue
    IFS=',' read -r -a services <<<"$restart"
    for service in "${services[@]+"${services[@]}"}"; do
      [ -n "$service" ] || continue
      if name="$(docker_apps_running_container "$project" "$service")"; then
        log "[${app_id}] restarting ${name} after Temporal route repair"
        docker restart "$name" >/dev/null || log "[${app_id}] restart of ${name} failed"
      fi
    done
    IFS=',' read -r -a labels <<<"$kick"
    for label in "${labels[@]+"${labels[@]}"}"; do
      [ -n "$label" ] || continue
      if launchctl print "gui/${uid}/${label}" >/dev/null 2>&1; then
        log "[${app_id}] kickstarting ${label} after Temporal route repair"
        launchctl kickstart -k "gui/${uid}/${label}" || true
      fi
    done
  done < <(docker_apps_query temporal-apps)
}

main() {
  if run_checks; then
    write_status "ok" "route, host TCP and container TCP checks passed"
    return 0
  fi

  if [ "$REPAIR" != "1" ]; then
    write_status "failed" "checks failed and repair is disabled"
    return 1
  fi
  if ! repair_allowed; then
    write_status "failed" "checks failed; repair skipped by cooldown"
    return 1
  fi

  repair_tailscale_route
  sleep "$POST_REPAIR_DELAY_SECONDS"

  if run_checks; then
    restart_after_repair
    write_status "repaired" "checks passed after Tailscale repair; workers restarted"
    return 0
  fi

  write_status "failed" "checks still failing after Tailscale repair"
  return 1
}

# Hand-rolled deadline: run main in the background and kill it if it hangs (a wedged
# Docker daemon can make `docker exec` block forever).
main "$@" &
main_pid=$!
elapsed=0
while kill -0 "$main_pid" 2>/dev/null; do
  if (( elapsed >= DEADLINE_SECONDS )); then
    kill -9 "$main_pid" 2>/dev/null || true
    wait "$main_pid" 2>/dev/null || true
    rm -f "$STATE_DIR/.targets.$$"
    write_status "timeout" "run exceeded ${DEADLINE_SECONDS}s and was killed"
    log "run exceeded ${DEADLINE_SECONDS}s and was killed"
    exit 124
  fi
  sleep 1
  elapsed=$(( elapsed + 1 ))
done
wait "$main_pid"
