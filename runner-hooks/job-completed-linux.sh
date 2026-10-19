#!/usr/bin/env bash
# The Linux runner tracks child processes itself. Reap only older processes
# whose working directory still belongs to this runner's finished workspace.
set -uo pipefail
[[ "${1:-}" == --inner ]] || { bash "$0" --inner "$@" || echo 'CI process cleanup failed' >&2; exit 0; }
shift
root="${RUNNER_WORKSPACE:-${GITHUB_WORKSPACE:-}}"
[[ -n "$root" ]] || exit 0
while [[ "$root" != / && ! "$(basename "$root")" =~ ^_work[0-9]*$ ]]; do root="$(dirname "$root")"; done
[[ "$root" != / && -d "$root" ]] || exit 0
root="$(cd "$root" && pwd -P)"
for proc in /proc/[0-9]*; do
  pid="${proc##*/}"
  [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" ]] || continue
  cwd="$(readlink "$proc/cwd" 2>/dev/null)" || continue
  [[ "$cwd" == "$root" || "$cwd" == "$root"/* ]] || continue
  command="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null)" || continue
  [[ "$command" =~ Runner\.(Worker|Listener)|dockerd|containerd|sshd ]] && continue
  age="$(ps -p "$pid" -o etimes= 2>/dev/null | tr -d ' ')"
  [[ "$age" =~ ^[0-9]+$ ]] || continue
  (( age >= 120 )) || continue
  echo "runner-reap: stopping old workspace process pid=$pid age=${age}s"
  [[ "${RUNNER_REAP_DRY_RUN:-0}" == 1 ]] && continue
  kill -TERM "$pid" 2>/dev/null || true
done
