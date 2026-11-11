#!/usr/bin/env bash
# Installs a locally configured Cloudflare tunnel connector for one compose project.
# Run as root from a private directory holding tunnel.json (credentials) and config.yml:
#   install-tunnel.sh <project> <slice>      e.g. trace-learn-prod jud-prod.slice
# The credentials file is moved into /srv/jud/tunnels/<project> (65532, 0600) and removed
# from the source directory. Idempotent.
set -euo pipefail
PROJECT="${1:?project}"; SLICE="${2:?slice}"
SRC="$PWD"; D="/srv/jud/tunnels/$PROJECT"
IMG="$(docker inspect jud-trace-learn-staging-tunnel --format '{{.Config.Image}}')"
mkdir -p "$D"; cp "$SRC/tunnel.json" "$SRC/config.yml" "$D/"; chmod 700 "$D"; chmod 600 "$D"/*; chown -R 65532:65532 "$D"
docker rm -f "jud-$PROJECT-tunnel" >/dev/null 2>&1 || true
docker run -d --name "jud-$PROJECT-tunnel" --restart unless-stopped --network host --user 65532:65532 \
  --cgroup-parent "$SLICE" --memory 256m --log-opt max-size=10m --log-opt max-file=3 \
  -v "$D:/etc/cloudflared:ro" "$IMG" tunnel --no-autoupdate --config /etc/cloudflared/config.yml run
rm -f "$SRC/tunnel.json"
sleep 8; docker ps --filter name="jud-$PROJECT-tunnel" --format '{{.Names}} {{.Status}}'; docker logs "jud-$PROJECT-tunnel" 2>&1 | grep -c "Registered tunnel connection"
