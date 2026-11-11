#!/usr/bin/env bash
# Deploys a new jud-observability collector release (four routes). Run as root.
# Usage (as root, from a directory holding the rendered config.json and compose.json):
#   apply-minisforum.sh --dry-run | --apply
# Render first: build.py --profile minisforum > config.json; build.py --profile minisforum --compose > compose.json
# Validates with the pinned image before replacing anything and rolls back to the previous
# release if the new collector is not healthy. current.json keeps target minisforum-staging
# because Octospark's prepare_host.py refuses any other value.
set -euo pipefail
STATE=/srv/jud/observability; SRC="$PWD"
IMG="$(python3 -c "import json;print(json.load(open('$SRC/compose.json'))['services']['host-collector']['image'])")"
PREV="$(python3 -c "import json;print(json.load(open('$STATE/current.json'))['release'])")"
mode="${1:---dry-run}"
if [[ "$mode" == --dry-run ]]; then
  echo "would: create $STATE/queue-shared and $STATE/credentials (10001:10001), copy $PREV/credentials/application-default-credentials.json"
  echo "would: write a new release under $STATE/releases/, validate config with $IMG"
  echo "would: docker compose -p jud-observability up -d --no-deps host-collector (replaces container jud-host-otel; previous release $PREV kept)"
  echo "would: add octospark-prod and trace-learn-prod to current.json projects, point release at the new directory"
  exit 0
fi
[[ "$mode" == --apply ]] || { echo "unknown mode" >&2; exit 2; }
mkdir -p "$STATE/queue-shared" "$STATE/credentials"
cp "$PREV/credentials/application-default-credentials.json" "$STATE/credentials/application-default-credentials.json"
chmod 700 "$STATE/queue-shared" "$STATE/credentials"; chmod 600 "$STATE/credentials/application-default-credentials.json"
chown -R 10001:10001 "$STATE/queue-shared" "$STATE/credentials"
REL="$STATE/releases/$(python3 -c 'import uuid;print(uuid.uuid4().hex)')"
install -d -m 700 "$REL"; install -m 644 "$SRC/config.json" "$REL/config.json"; install -m 600 "$SRC/compose.json" "$REL/compose.json"
docker run --rm --user 10001:10001 -v "$REL/config.json:/etc/otel-collector/config.json:ro" -v "$STATE/credentials:/var/secrets/google:ro" -v /proc:/hostfs/proc:ro -v /sys:/hostfs/sys:ro -v "$STATE/queue-shared:/var/lib/shared-queue" \
  -e GOOGLE_APPLICATION_CREDENTIALS=/var/secrets/google/application-default-credentials.json "$IMG" validate --config=/etc/otel-collector/config.json
echo "config valid"
cp -p "$STATE/current.json" "$STATE/current.json.before-$(basename "$REL")"
docker compose -p jud-observability -f "$REL/compose.json" up -d --no-deps host-collector
ok=0; for _ in $(seq 1 30); do curl -fsS -o /dev/null http://127.0.0.1:32133/health/status && { ok=1; break; }; sleep 2; done
if [[ $ok != 1 ]]; then
  echo "new collector unhealthy: rolling back to $PREV" >&2; docker logs --tail 30 jud-host-otel >&2 || true
  docker compose -p jud-observability -f "$PREV/compose.json" up -d --no-deps host-collector; exit 1
fi
python3 - "$STATE/current.json" "$REL" "$IMG" <<'PY'
import json,sys,time,os
path,rel,img=sys.argv[1:]
d=json.load(open(path))
d['previous_release']=d['release']; d['release']=rel; d['image']=img; d['deployedAt']=time.time()
d['projects'].update({'octospark-prod':'octospark-prod','trace-learn-prod':'tracelearn-prod'})
d['source']={'repository':'jamesaphoenix/agent-scripts','branch':'feat/minisforum-hardening','path':'host-observability','profile':'minisforum'}
tmp=path+'.pending'; json.dump(d,open(tmp,'w'),indent=2); os.chmod(tmp,0o644); os.replace(tmp,path)
PY
echo "deployed $REL (previous $PREV)"
