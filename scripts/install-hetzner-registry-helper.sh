#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ $(id -u) != 0 || $# != 2 ]]; then
  echo 'Usage (as root): install-hetzner-registry-helper.sh EXISTING_ADC_KEY WRAPPER_SOURCE' >&2
  exit 1
fi
key_source=$1
wrapper_source=$2
task_tmp=$(mktemp -d)
trap 'rm -rf "$task_tmp"' EXIT
curl --fail --silent --show-error --location \
  https://github.com/GoogleCloudPlatform/docker-credential-gcr/releases/download/v2.2.1/docker-credential-gcr_linux_amd64-2.2.1.tar.gz \
  --output "$task_tmp/helper.tar.gz"
printf '%s  %s\n' 54733a03c18f230059d89e8976518de9af16cebb74b86ccab5c95c9da66f5912 "$task_tmp/helper.tar.gz" | sha256sum --check --status
tar -xzf "$task_tmp/helper.tar.gz" -C "$task_tmp" docker-credential-gcr
install -m 755 "$task_tmp/docker-credential-gcr" /usr/local/bin/docker-credential-gcr
install -m 755 "$wrapper_source" /usr/local/bin/docker-credential-jud-gcr
install -d -m 700 /root/.config/gcloud /root/.docker /root/.local/state/jud-registry/backups
python3 - "$key_source" <<'PY'
import json, os, pathlib, shutil, sys, time
source = pathlib.Path(sys.argv[1])
key = json.loads(source.read_text())
assert key['type'] == 'service_account' and key.get('private_key')
backup = pathlib.Path('/root/.local/state/jud-registry/backups') / str(time.time_ns())
backup.mkdir(mode=0o700)
adc = pathlib.Path('/root/.config/gcloud/application_default_credentials.json')
config = pathlib.Path('/root/.docker/config.json')
for p in (adc, config):
    assert not p.is_symlink()
    if p.exists():
        shutil.copy2(p, backup / p.name)
        os.chmod(backup / p.name, 0o600)
data = json.loads(config.read_text()) if config.exists() else {}
data.setdefault('credHelpers', {})['europe-west3-docker.pkg.dev'] = 'jud-gcr'
data.get('auths', {}).pop('europe-west3-docker.pkg.dev', None)
for path, content in ((adc, source.read_text()), (config, json.dumps(data, indent=2))):
    temporary = path.with_suffix('.new')
    with temporary.open('w') as output:
        os.chmod(temporary, 0o600)
        output.write(content)
    temporary.replace(path)
print('Installed refreshable registry credentials for', key['client_email'])
print('Rollback backup:', backup)
PY
docker-credential-gcr config --token-source=env >/dev/null
