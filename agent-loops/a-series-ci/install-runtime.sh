#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo 'Run as root on A Series.' >&2; exit 1; }
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
target=/opt/jud-ci

install -d -m 755 "$target/lib" "$target/runner-hooks" "$target/agent-loops/cache-hygiene" "$target/agent-loops/a-series-ci"
for file in lib/ci-admission.py lib/op-service-account-env.sh; do
  install -m 644 "$source_root/$file" "$target/$file"
done
for file in runner-hooks/job-started.sh runner-hooks/job-completed-linux.sh runner-hooks/setup-deploy-ssh.sh; do
  install -m 755 "$source_root/$file" "$target/$file"
done
install -m 644 "$source_root/runner-hooks/a-series-deploy-known-hosts" "$target/runner-hooks/a-series-deploy-known-hosts"
install -m 755 "$source_root/agent-loops/cache-hygiene/disk-preflight.py" "$target/agent-loops/cache-hygiene/disk-preflight.py"
install -m 755 "$source_root/agent-loops/a-series-ci/cleanup.py" "$target/agent-loops/a-series-ci/cleanup.py"
install -m 644 "$source_root/agent-loops/a-series-ci/jud-ci-cleanup.service" /etc/systemd/system/jud-ci-cleanup.service
install -m 644 "$source_root/agent-loops/a-series-ci/jud-ci-cleanup.timer" /etc/systemd/system/jud-ci-cleanup.timer
systemctl daemon-reload
systemctl enable --now jud-ci-cleanup.timer
echo 'A Series CI runtime installed. Restart idle runner services to load updated hooks.'
