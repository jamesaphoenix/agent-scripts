# A Series GitHub Actions host

Four organisation runners, `a-series-runner-1` through `-4`, run as the
dedicated `ci-runner` account. The account has no Docker group
membership (the group grants host root). It uses its own rootless Docker daemon at
`unix:///run/user/1001/docker.sock`, with its data under
`/home/ci-runner/.local/share/docker`, so CI never shares a daemon with staging or
production. See `rootless/README.md`. It must never receive James's dotfiles,
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
Run `DOCKER_HOST=... python3 /opt/jud-ci/agent-loops/a-series-ci/cleanup.py` for a dry run.

Every Docker call in `cleanup.py` and `cache-cleanup.sh` takes an explicit `DOCKER_HOST`
(there is no default). Volume, image and builder prunes, and the BuildKit prune, run only
when it is the CI daemon socket `unix:///run/user/1001/docker.sock` and are refused
otherwise, so they can never reach the daemon that also runs staging and production.
The units and runner template set it to the rootless socket, so install them after
`rootless/` is applied (before that they cannot connect and fail safely). Pointed at any
other daemon the scripts only remove label-scoped ephemeral containers and checkouts. Tests: `python3 -m unittest discover -s tests -p 'test_a_series_cleanup.py'`.

`jud-ci-cache-cleanup.timer` runs weekly (Sunday 03:30 host time). It is the only
place the persistent CI caches under `/home/ci-runner/.cache/octospark-ci` and
`/home/ci-runner/.cache/trace-learn-ci` (pnpm store, Turbo, local BuildKit
caches) are removed: jobs never prune them. `cache-cleanup.sh` runs each
repository's own `scripts/deploy/ci/host-cache-cleanup.sh` from the newest
runner workspace checkout (entries unused for 14 days, `pnpm store prune`) and
then prunes the `octospark-cache-builder` and `trace-learn-cache-builder`
BuildKit containers through `buildctl` (14 days, 40 GB kept). Run
`sudo -u ci-runner /opt/jud-ci/agent-loops/a-series-ci/cache-cleanup.sh --dry-run`
to see what it would remove.
Pause with `/home/ci-runner/.local/state/a-series-ci/disabled`.

After updating this source on the MacBook, copy only the maintained `lib/`,
`runner-hooks/`, and `agent-loops/a-series-ci/` paths to `/opt/jud-ci/` on A
Series, set ownership to root and modes to 755, then restart the runners.

## Rebuild

On a fresh Ubuntu 26.04 A Series host, install Docker Engine, Buildx,
Compose, `qemu-user-binfmt`, `ffmpeg`, Go, the 1Password CLI and Google Cloud CLI.
Install the official Node 22 build with `sudo agent-loops/a-series-ci/install-node22.sh`.
Ubuntu's Node 22 package on this host lacks TypeScript type stripping, which
the auth worker tests need. The installer verifies Node's published SHA-256
checksum and links Node/Corepack under `/usr/local/bin`. Install the CLIs
from their signed vendor APT repositories. Confirm `qemu-aarch64` is
registered in `/proc/sys/fs/binfmt_misc/`. The shared daemon keeps `/var/run/docker.sock`
for the staging and production stacks (see `daemon/`); CI uses the rootless socket.
Run `sudo agent-loops/a-series-ci/rootless/setup-ci-rootless-docker.sh --check`, then
the script itself, before provisioning runners.

From a checked-out Octospark workspace with dependencies installed, run
`sudo env PATH=/usr/local/bin:/usr/bin:/bin pnpm --filter @tx-agent-kit/web exec playwright install-deps chromium`
once to install Chromium's Linux system libraries and fonts. Browser jobs then
run `playwright install chromium` as `ci-runner` without sudo.

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
