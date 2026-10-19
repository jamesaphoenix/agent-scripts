# A Series GitHub Actions host

Four organisation runners, `a-series-runner-1` through `-4`, run as the
dedicated `ci-runner` account. The account has standard Docker group access,
which grants effective host-root access; it must never receive James's dotfiles,
home directory, SSH agent, or a persistent 1Password service token.

The runner service `.env` files set `TX_RUNNER_SLOT=0..3` and point to the
started/completed hooks in `/opt/jud-ci/runner-hooks/`. The started hook checks
disk and admits at most two heavy jobs. Linux uses `/` for the disk check;
the floor is 100 GiB plus 20 GiB for each active worker. Control jobs retain
their exact allowlist in `lib/ci-admission.py`.

`jud-ci-cleanup.timer` runs daily at 04:10 host time. It removes only detached
CI containers labelled with a dead creator PID and age at least three hours,
anonymous unattached volumes, dangling images, build cache older than seven
days, and runner checkouts idle for seven days. It skips cache and checkout
pruning while any job is active. It never prunes named volumes or tagged images.
Run `python3 /opt/jud-ci/agent-loops/a-series-ci/cleanup.py` for a dry run.
Pause with `/home/ci-runner/.local/state/a-series-ci/disabled`.

After updating this source on the MacBook, copy only the maintained `lib/`,
`runner-hooks/`, and `agent-loops/a-series-ci/` paths to `/opt/jud-ci/` on A
Series, set ownership to root and modes to 755, then restart the runners.

## Rebuild

On a fresh Ubuntu 26.04 A Series host, install Docker Engine, Buildx,
Compose, `qemu-user-binfmt`, Go, Node 22/Corepack, the 1Password CLI and
Google Cloud CLI. The first five are Ubuntu packages; install the last two
from their signed vendor APT repositories. Confirm `qemu-aarch64` is
registered in `/proc/sys/fs/binfmt_misc/`, and confirm the Docker socket is
`/var/run/docker.sock`.

Create the GitHub organisation runner group `a-series-ci` with access only to
`octospark` and `smart-cub-learning`, and disable public repositories. From
this checkout on A Series, run `sudo agent-loops/a-series-ci/install-runtime.sh`.
Pass a short-lived organisation runner registration token on stdin to
`sudo agent-loops/a-series-ci/provision-runners.sh`. That script verifies the
pinned GitHub runner archive, registers four runner services and sets their
slot and hook environments. Do not save the registration token. If the runner
group or repository access changes, verify them through the GitHub API.

Release jobs read `op://api-keys/A Series CI deploy SSH/private_key` once per
job and write it only inside `RUNNER_TEMP`. The matching public key is
authorised for `mydocker@100.123.72.113`, `root@100.81.18.118`, and
`jamesphoenix@jamess-mac-studio`. Host keys in
`runner-hooks/a-series-deploy-known-hosts` were checked against the MacBook's
existing known hosts during migration. Keep this file pinned when rebuilding.
GitHub injects the 1Password service token and Google service-account JSON
into the relevant jobs; neither belongs in the runner home.

Run `sudo agent-loops/a-series-ci/install-runtime.sh` after pulling changes
on A Series, then restart each idle `jud-actions-runner-N.service`. Check
`systemctl status jud-ci-cleanup.timer` and the GitHub runner group before
using the host for releases.
