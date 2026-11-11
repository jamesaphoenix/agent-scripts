#!/usr/bin/env bash
# Installs an OpenSSH server that listens on loopback only and admits only jud-deploy by key.
# The A Series has no sshd (remote access is Tailscale SSH), but production deploys reach the
# deploy user over SSH from the runner on this same host. Idempotent. Run as root.
#   setup-loopback-sshd.sh --dry-run | --apply
set -euo pipefail
CONF=/etc/ssh/sshd_config.d/10-jud-deploy.conf
WANT='# Managed by agent-scripts (a-series-ci/deploy-user/setup-loopback-sshd.sh).
ListenAddress 127.0.0.1
ListenAddress 127.0.1.1
AllowUsers jud-deploy
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no'
if [[ "${1:---dry-run}" != --apply ]]; then
  echo "would write $CONF:"; printf '%s\n' "$WANT" | sed 's/^/    /'
  echo "would run: apt-get install -y --no-install-recommends openssh-server"
  echo "would verify: port 22 listens on 127.0.0.1 and 127.0.1.1 only, otherwise stop ssh and fail"
  exit 0
fi
install -d -m 755 /etc/ssh/sshd_config.d
printf '%s\n' "$WANT" > "$CONF"; chmod 644 "$CONF"
dpkg -s openssh-server >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y --no-install-recommends openssh-server
sshd -t
systemctl daemon-reload
systemctl restart ssh.socket 2>/dev/null || true
systemctl restart ssh 2>/dev/null || systemctl restart ssh.service
sleep 2
LISTEN="$(ss -ltnH 'sport = :22' | awk '{print $4}' | sort -u | tr '\n' ' ')"
echo "listening: $LISTEN"
for addr in $LISTEN; do
  case "$addr" in 127.0.0.1:22|127.0.1.1:22) ;; *) echo "sshd is listening on $addr: stopping it" >&2; systemctl stop ssh.socket ssh 2>/dev/null || true; exit 1 ;; esac
done
[[ -n "$LISTEN" ]] || { echo "sshd is not listening" >&2; exit 1; }
sshd -T 2>/dev/null | grep -E "^(allowusers|passwordauthentication|permitrootlogin|listenaddress) " | tr '\n' ';'; echo
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub | awk '{print "host key", $2}'
