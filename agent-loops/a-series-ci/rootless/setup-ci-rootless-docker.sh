#!/usr/bin/env bash
# Give the CI runners their own rootless Docker daemon, away from the production daemon.
#
# WHY: ci-runner is in the `docker` group, which is root on the host, and CI shares one
# daemon with staging and (soon) production stacks. After this script, CI jobs talk to a
# rootless daemon owned by ci-runner; the group membership is removed.
#
# Result (ci-runner uid 1001):
#   data-root  /home/ci-runner/.local/share/docker
#   socket     /run/user/1001/docker.sock     (DOCKER_HOST=unix:///run/user/1001/docker.sock)
#   published ports default to 127.0.0.1 (daemon "ip" setting), so CI services never listen
#   on the Tailscale or LAN addresses.
#
# Idempotent: every step checks current state first and does nothing if already done.
# NOT run by the author of this change. Read README.md ("Apply", "Rollback") first.
#
#   setup-ci-rootless-docker.sh --check              report state only, change nothing
#   setup-ci-rootless-docker.sh --dry-run            print every change it would make
#   setup-ci-rootless-docker.sh [--phase NAME ...]   apply (default: all phases, in order)
#   phases: install runners builders services group
#   --restart-runners   with the runners phase, restart runner services that are idle
#
# Phases after `install` need an idle host (no Runner.Worker); they refuse otherwise.
set -euo pipefail

CI_USER=${CI_USER:-ci-runner}
CI_UID=${CI_UID:-1001}
CI_HOME=${CI_HOME:-/home/$CI_USER}
RUN_DIR=${RUN_DIR:-/run/user/$CI_UID}
CI_SOCKET=${CI_SOCKET:-$RUN_DIR/docker.sock}
CI_DOCKER_HOST="unix://$CI_SOCKET"
OLD_DOCKER_HOST=${OLD_DOCKER_HOST:-unix:///var/run/docker.sock}
DATA_ROOT=${DATA_ROOT:-$CI_HOME/.local/share/docker}
SUBUID_FILE=${SUBUID_FILE:-/etc/subuid}
SUBGID_FILE=${SUBGID_FILE:-/etc/subgid}
CONTRIB=${CONTRIB:-/usr/share/docker.io/contrib}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${TEMPLATE:-$SCRIPT_DIR/../runner.service.template}"
PACKAGES=(uidmap rootlesskit slirp4netns iptables dbus-user-session)
BUILDERS=(octospark-cache-builder trace-learn-cache-builder)
CI_PROJECTS=(octospark-ci trace-learn-ci)
ALL_PHASES=(install runners builders services group)

MODE=apply
PHASES=()
RESTART_RUNNERS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check ;;
    --dry-run) MODE=dry ;;
    --phase) shift; PHASES+=("${1:?--phase needs a name}") ;;
    --restart-runners) RESTART_RUNNERS=1 ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ ${#PHASES[@]} -gt 0 ]] || PHASES=("${ALL_PHASES[@]}")
for p in "${PHASES[@]}"; do
  [[ " ${ALL_PHASES[*]} " == *" $p "* ]] || { echo "unknown phase: $p" >&2; exit 2; }
done

say() { printf '%s\n' "$*"; }
# Every mutating command goes through `do_it` so --dry-run prints instead of executing.
do_it() {
  if [[ "$MODE" == dry ]]; then say "would run: $*"; else say "+ $*"; "$@"; fi
}
# Run a command as ci-runner with its user-manager environment and the rootless daemon.
as_ci() {
  runuser -u "$CI_USER" -- env "XDG_RUNTIME_DIR=$RUN_DIR" \
    "DBUS_SESSION_BUS_ADDRESS=unix:path=$RUN_DIR/bus" "DOCKER_HOST=$CI_DOCKER_HOST" \
    "PATH=$CONTRIB:/usr/local/bin:/usr/bin:/bin" "$@"
}
as_ci_do() {
  if [[ "$MODE" == dry ]]; then say "would run as $CI_USER: $*"; else say "+ ($CI_USER) $*"; as_ci "$@"; fi
}
old_docker() { DOCKER_HOST="$OLD_DOCKER_HOST" docker "$@"; }
active_jobs() { pgrep -f Runner.Worker >/dev/null 2>&1; }
need_idle() {
  if active_jobs; then
    if [[ "$MODE" == dry ]]; then say "note: runner jobs are active now; a real run would stop here"; return 0; fi
    say "refusing: Runner.Worker is active. Wait for idle runners or stop them first." >&2; exit 1
  fi
}

# ---- state probes (used by --check and by the idempotency guards) -------------------
has_pkgs() { local p; for p in "${PACKAGES[@]}"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || return 1; done; }
has_subids() { grep -q "^$CI_USER:" "$SUBUID_FILE" 2>/dev/null && grep -q "^$CI_USER:" "$SUBGID_FILE" 2>/dev/null; }
has_linger() { [[ "$(loginctl show-user "$CI_USER" -p Linger --value 2>/dev/null || true)" == yes ]]; }
has_delegate() { [[ -f /etc/systemd/system/user@.service.d/jud-delegate.conf ]]; }
has_socket() { [[ -S "$CI_SOCKET" ]]; }
in_docker_group() { id -nG "$CI_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; }
rootless_unit() { as_ci systemctl --user cat docker.service >/dev/null 2>&1; }
rootless_active() { [[ "$(as_ci systemctl --user is-active docker.service 2>/dev/null || true)" == active ]]; }
template_ok() { grep -q "^Environment=DOCKER_HOST=$CI_DOCKER_HOST\$" "$TEMPLATE" 2>/dev/null; }
ci_docker() { as_ci docker "$@"; }
builder_up() { ci_docker container inspect "buildx_buildkit_${1}0" >/dev/null 2>&1; }
service_up() { [[ -n "$(ci_docker ps -q --filter "label=com.docker.compose.project=$1" 2>/dev/null || true)" ]]; }

FAILED=0
# chk LABEL COMMAND...: print [ok] or [todo]; never aborts under set -e.
chk() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '  [ok]   %s\n' "$label"; else printf '  [todo] %s\n' "$label"; FAILED=1; fi
}
not_in_docker_group() { ! in_docker_group; }
uid_matches() { [[ "$(id -u "$CI_USER" 2>/dev/null)" == "$CI_UID" ]]; }
check() {
  say "Rootless Docker for $CI_USER (uid $CI_UID): current state ($MODE)"
  chk "user $CI_USER has uid $CI_UID" uid_matches
  chk "subuid/subgid ranges for $CI_USER" has_subids
  chk "packages: ${PACKAGES[*]}" has_pkgs
  say "  info: kernel.apparmor_restrict_unprivileged_userns=$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo n/a) (1 needs the rootlesskit AppArmor profile)"
  chk "cgroup delegation drop-in for user@.service" has_delegate
  chk "linger enabled" has_linger
  chk "user unit docker.service installed" rootless_unit
  chk "rootless dockerd active" rootless_active
  chk "socket $CI_SOCKET" has_socket
  chk "runner.service.template sets DOCKER_HOST=$CI_DOCKER_HOST" template_ok
  local b s
  for b in "${BUILDERS[@]}"; do chk "builder $b on rootless daemon" builder_up "$b"; done
  for s in "${CI_PROJECTS[@]}"; do chk "CI services $s on rootless daemon" service_up "$s"; done
  chk "$CI_USER removed from docker group" not_in_docker_group
  say "  data-root: $DATA_ROOT; shared daemon untouched: $OLD_DOCKER_HOST"
  return "$FAILED"
}

# ---- phases ---------------------------------------------------------------------------
phase_install() {
  has_subids || { say "missing $SUBUID_FILE/$SUBGID_FILE entries for $CI_USER (needs >= 65536 ids); add them, then rerun" >&2; exit 1; }
  has_pkgs || do_it apt-get install -y --no-install-recommends "${PACKAGES[@]}"
  # Ubuntu restricts unprivileged user namespaces; the packaged rootlesskit AppArmor profile permits them.
  if [[ "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" == 1 ]] && [[ ! -e /etc/apparmor.d/rootlesskit ]]; then
    say "kernel.apparmor_restrict_unprivileged_userns=1 and no /etc/apparmor.d/rootlesskit profile: dockerd would fail. Install the profile for /usr/bin/rootlesskit first." >&2; exit 1
  fi
  # Delegate cgroup controllers to ci-runner's user manager so `--memory`/`--cpus` work in rootless mode.
  if ! has_delegate; then
    do_it install -d -m 755 /etc/systemd/system/user@.service.d
    if [[ "$MODE" == dry ]]; then say "would write /etc/systemd/system/user@.service.d/jud-delegate.conf (Delegate=cpu cpuset io memory pids)"
    else printf '[Service]\nDelegate=cpu cpuset io memory pids\n' > /etc/systemd/system/user@.service.d/jud-delegate.conf; fi
    do_it systemctl daemon-reload
  fi
  has_linger || do_it loginctl enable-linger "$CI_USER"
  if [[ "$MODE" != dry ]]; then
    for _ in $(seq 1 30); do [[ -S "$RUN_DIR/bus" ]] && break; sleep 1; done
    [[ -S "$RUN_DIR/bus" ]] || { echo "user manager for $CI_USER did not start" >&2; exit 1; }
  fi
  # Daemon config: explicit data-root, systemd cgroup driver, loopback-only default publish address, bounded logs.
  local cfg="$CI_HOME/.config/docker/daemon.json"
  local want='{"data-root":"'"$DATA_ROOT"'","exec-opts":["native.cgroupdriver=systemd"],"ip":"127.0.0.1","log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}'
  if [[ "$(cat "$cfg" 2>/dev/null || true)" != "$want" ]]; then
    if [[ "$MODE" == dry ]]; then say "would write $cfg: $want"
    else
      install -d -o "$CI_USER" -g "$CI_USER" -m 700 "$CI_HOME/.config/docker"
      printf '%s\n' "$want" > "$cfg"; chown "$CI_USER:$CI_USER" "$cfg"; chmod 600 "$cfg"
    fi
  fi
  if ! rootless_unit; then
    as_ci_do dockerd-rootless-setuptool.sh install
  fi
  rootless_active || as_ci_do systemctl --user enable --now docker.service
  if [[ "$MODE" != dry ]]; then
    for _ in $(seq 1 30); do has_socket && break; sleep 1; done
    has_socket || { echo "rootless socket $CI_SOCKET did not appear; see: runuser -u $CI_USER -- journalctl --user -u docker" >&2; exit 1; }
    ci_docker info --format '{{.DockerRootDir}} rootless={{range .SecurityOptions}}{{.}} {{end}}'
  fi
}

phase_runners() {
  need_idle
  template_ok || { say "runner.service.template must set Environment=DOCKER_HOST=$CI_DOCKER_HOST (it is in the repo; pull it)" >&2; exit 1; }
  local slot unit
  for slot in 1 2 3 4; do
    unit="/etc/systemd/system/jud-actions-runner-$slot.service"
    [[ -f "$unit" ]] || continue
    if [[ "$MODE" == dry ]]; then say "would render $unit from $TEMPLATE (slot $slot) if it differs"
    elif ! diff -q <(sed "s/@SLOT@/$slot/g" "$TEMPLATE") "$unit" >/dev/null 2>&1; then
      sed "s/@SLOT@/$slot/g" "$TEMPLATE" > "$unit"; say "updated $unit"
    fi
  done
  do_it systemctl daemon-reload
  # CI cleanup units use the rootless socket too.
  local u
  for u in jud-ci-cleanup jud-ci-cache-cleanup; do
    local f="/etc/systemd/system/$u.service"
    if [[ -f "$f" ]] && ! grep -q "DOCKER_HOST=$CI_DOCKER_HOST" "$f"; then
      do_it sed -i "s#^Environment=DOCKER_HOST=.*#Environment=DOCKER_HOST=$CI_DOCKER_HOST#; /^SupplementaryGroups=docker/d" "$f"
    fi
  done
  if [[ "$RESTART_RUNNERS" == 1 ]]; then
    for slot in 1 2 3 4; do do_it systemctl restart "jud-actions-runner-$slot.service"; done
  else
    say "runner units updated; restart each idle runner (or rerun with --restart-runners) to load DOCKER_HOST"
  fi
}

phase_builders() {
  need_idle
  has_socket || { echo "rootless daemon is not up (run the install phase)" >&2; exit 1; }
  local b cfg
  for b in "${BUILDERS[@]}"; do
    builder_up "$b" && { say "builder $b already on the rootless daemon"; continue; }
    cfg="$(mktemp -d)"
    # A throwaway DOCKER_CONFIG keeps buildx instance metadata out of the runner homes (jobs register their own);
    # --keep-daemon leaves the container running after the metadata is removed.
    as_ci_do env "DOCKER_CONFIG=$cfg" docker buildx create --name "$b" --driver docker-container \
      --buildkitd-flags '--allow-insecure-entitlement=network.host' --bootstrap
    as_ci_do env "DOCKER_CONFIG=$cfg" docker buildx rm --keep-daemon "$b"
    rm -rf "$cfg"
  done
}

phase_services() {
  need_idle
  has_socket || { echo "rootless daemon is not up (run the install phase)" >&2; exit 1; }
  local p ids first workdir files image
  for p in "${CI_PROJECTS[@]}"; do
    service_up "$p" && { say "$p already on the rootless daemon"; continue; }
    ids="$(old_docker ps -aq --filter "label=com.docker.compose.project=$p")"
    [[ -n "$ids" ]] || { say "no $p containers on the shared daemon; nothing to move"; continue; }
    first="$(echo "$ids" | head -1)"
    files="$(old_docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$first")"
    workdir="$(old_docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$first")"
    # Locally built images do not exist in the new daemon: copy them across (no secrets in this path).
    # shellcheck disable=SC2086
    for image in $(old_docker inspect --format '{{.Config.Image}}' $ids | sort -u); do
      if [[ "$MODE" == dry ]]; then say "would copy image $image to the rootless daemon (docker save | docker load) if missing"
      elif ! ci_docker image inspect "$image" >/dev/null 2>&1 && old_docker image inspect "$image" >/dev/null 2>&1; then
        old_docker save "$image" | ci_docker load
      fi
    done
    # shellcheck disable=SC2086  # $ids is a deliberate list of container IDs
    # Ports 15432/15433/16379/16380 are held by the old containers: stop (do not remove) them first.
    do_it env "DOCKER_HOST=$OLD_DOCKER_HOST" docker stop $ids
    # Published ports default to 127.0.0.1 through the rootless daemon's "ip" setting.
    as_ci_do docker compose -p "$p" -f "$files" --project-directory "$workdir" up -d --wait
  done
}

phase_group() {
  need_idle
  has_socket || { echo "refusing to drop the docker group: rootless daemon is not up" >&2; exit 1; }
  local b p
  for b in "${BUILDERS[@]}"; do builder_up "$b" || { echo "builder $b is not on the rootless daemon yet" >&2; exit 1; }; done
  template_ok || { echo "runner template does not point at the rootless socket" >&2; exit 1; }
  if in_docker_group; then do_it gpasswd -d "$CI_USER" docker; else say "$CI_USER is not in the docker group"; fi
  # Old CI containers and builders stay stopped on the shared daemon until you remove them deliberately
  # (see README "Rollback" and "Cleaning up the old CI containers"); nothing is deleted here.
  say "group membership applies to new sessions; restart the runner services so they lose it"
}

# ---- main -----------------------------------------------------------------------------
if [[ "$MODE" == check ]]; then
  check; exit $?
fi
[[ "$(id -u)" == 0 ]] || { echo 'Run as root (use --check to inspect without changes as any user).' >&2; exit 1; }
for phase in "${ALL_PHASES[@]}"; do
  [[ " ${PHASES[*]} " == *" $phase "* ]] || continue
  say "== phase: $phase"
  "phase_$phase"
done
say "done. Verify with: $0 --check"
