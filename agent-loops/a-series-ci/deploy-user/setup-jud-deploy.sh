#!/usr/bin/env bash
# Creates the dedicated production deploy user (docker group, key-only, no sudo), its
# state directory, the shared host deploy lock and the runner known_hosts entries.
# Idempotent. Run as root: setup-jud-deploy.sh <public-key-file> [project ...]
# The public key is op://api-keys/A Series CI deploy SSH/public_key.
set -euo pipefail
PUBKEY_FILE="${1:?public key file required}"; shift
PROJECTS=("${@:-trace-learn-prod}")
id jud-deploy >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker jud-deploy
usermod -aG docker jud-deploy
passwd -l jud-deploy >/dev/null
install -d -o jud-deploy -g jud-deploy -m 700 /home/jud-deploy/.ssh
printf 'restrict %s
' "$(cat "$PUBKEY_FILE")" > /home/jud-deploy/.ssh/authorized_keys
chown jud-deploy:jud-deploy /home/jud-deploy/.ssh/authorized_keys; chmod 600 /home/jud-deploy/.ssh/authorized_keys
for project in "${PROJECTS[@]}"; do install -d -o jud-deploy -g jud-deploy -m 700 "/srv/jud/$project"; done
[ -e /srv/jud/deploy.host.lock ] || : > /srv/jud/deploy.host.lock
chown root:docker /srv/jud/deploy.host.lock; chmod 660 /srv/jud/deploy.host.lock
K=/opt/jud-ci/runner-hooks/a-series-deploy-known-hosts
HK="$(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
for name in jamesaphoenix-ai-series 100.124.34.45; do grep -q "^$name " "$K" || printf '%s %s\n' "$name" "$HK" >> "$K"; done
id jud-deploy; ls -la /srv/jud | grep -E "prod|host.lock"; cut -c1-50 "$K"; sudo -l -U jud-deploy | tail -1
