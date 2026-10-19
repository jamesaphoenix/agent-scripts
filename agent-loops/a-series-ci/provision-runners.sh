#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo 'Run as root on A Series.' >&2; exit 1; }
for tool in curl sha256sum docker node corepack op gcloud go qemu-aarch64; do
  command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done
[[ -x /opt/jud-ci/runner-hooks/job-started.sh ]] || { echo 'Run install-runtime.sh first.' >&2; exit 1; }

# Pass a short-lived GitHub organisation registration token on stdin.
IFS= read -r registration_token
[[ -n "$registration_token" ]] || { echo 'Registration token is required on stdin.' >&2; exit 1; }
runner_version=2.338.0
runner_sha256=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32
archive="/tmp/actions-runner-linux-x64-${runner_version}.tar.gz"
if [[ ! -f "$archive" ]]; then
  curl -fsSL "https://github.com/actions/runner/releases/download/v${runner_version}/actions-runner-linux-x64-${runner_version}.tar.gz" -o "$archive"
fi
printf '%s  %s\n' "$runner_sha256" "$archive" | sha256sum -c -

id ci-runner >/dev/null 2>&1 || useradd --create-home --shell /bin/bash ci-runner
usermod -aG docker ci-runner
chmod 750 /home/ci-runner
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for slot in 1 2 3 4; do
  directory="/home/ci-runner/actions-runner-$slot"
  if [[ ! -f "$directory/.runner" ]]; then
    mkdir -p "$directory"
    tar -xzf "$archive" -C "$directory"
    chown -R ci-runner:ci-runner "$directory"
    (
      cd "$directory"
      runuser -u ci-runner -- ./config.sh \
        --url https://github.com/Just-Understanding-Data-Ltd \
        --token "$registration_token" \
        --name "a-series-runner-$slot" \
        --runnergroup a-series-ci \
        --labels a-series \
        --work _work \
        --unattended --replace
    )
  fi
  {
    printf 'TX_RUNNER_SLOT=%d\n' "$((slot - 1))"
    printf 'ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/jud-ci/runner-hooks/job-started.sh\n'
    printf 'ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/jud-ci/runner-hooks/job-completed-linux.sh\n'
    printf 'AGENT_SCRIPTS_ROOT=/opt/jud-ci\n'
  } > "$directory/.env"
  chown ci-runner:ci-runner "$directory/.env"
  chmod 600 "$directory/.env"
  sed "s/@SLOT@/$slot/g" "$source_root/runner.service.template" > "/etc/systemd/system/jud-actions-runner-$slot.service"
done
unset registration_token
systemctl daemon-reload
for slot in 1 2 3 4; do
  systemctl enable --now "jud-actions-runner-$slot.service"
done
