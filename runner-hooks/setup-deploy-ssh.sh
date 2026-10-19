#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"
: "${GITHUB_PATH:?GITHUB_PATH is required}"
: "${OP_SERVICE_ACCOUNT_TOKEN:?OP_SERVICE_ACCOUNT_TOKEN is required}"

runtime_root="${AGENT_SCRIPTS_ROOT:-/opt/jud-ci}"
source_hosts="$runtime_root/runner-hooks/a-series-deploy-known-hosts"
test -r "$source_hosts"

ssh_dir="$RUNNER_TEMP/deploy-ssh"
mkdir -p "$ssh_dir/bin"
chmod 700 "$ssh_dir" "$ssh_dir/bin"
op read 'op://api-keys/A Series CI deploy SSH/private_key' > "$ssh_dir/id_ed25519"
chmod 600 "$ssh_dir/id_ed25519"
ssh-keygen -y -f "$ssh_dir/id_ed25519" >/dev/null
cp "$source_hosts" "$ssh_dir/known_hosts"
chmod 600 "$ssh_dir/known_hosts"

for tool in ssh scp; do
  cat > "$ssh_dir/bin/$tool" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
tool="${0##*/}"
exec "/usr/bin/$tool" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=${CI_DEPLOY_SSH_KNOWN_HOSTS:?}" \
  -i "${CI_DEPLOY_SSH_KEY:?}" "$@"
WRAPPER
  chmod 700 "$ssh_dir/bin/$tool"
done

{
  printf 'CI_DEPLOY_SSH_KEY=%s\n' "$ssh_dir/id_ed25519"
  printf 'CI_DEPLOY_SSH_KNOWN_HOSTS=%s\n' "$ssh_dir/known_hosts"
  printf 'RSYNC_RSH=%s/bin/ssh\n' "$ssh_dir"
} >> "$GITHUB_ENV"
printf '%s\n' "$ssh_dir/bin" >> "$GITHUB_PATH"
echo 'Deployment SSH identity prepared for this job.'
