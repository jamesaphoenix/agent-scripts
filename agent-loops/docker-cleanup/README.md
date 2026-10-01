# Host Docker janitor

Reclaims stale release images and BuildKit cache on a shared Docker daemon.
Host-level, cross-product: CI runners, production, and every sibling product
(octospark, trace-learn) share one `dockerd` on the Mac Studio (and, via its
own cron install, on the Hetzner standby box). This is why the script lives
here instead of in any one product repo - a copy embedded in a product repo
drifts the moment a new sibling product starts tagging images on the host.

## What changed from the original octospark-only version

The original allowlist-based protection (`docker ps -a --filter
label=com.docker.compose.project=octospark-staging`) had silently protected
nothing for a long time: the live stacks run as `octospark-staging-live` /
`octospark-prod-live`, not the names the allowlist matched. Protection now
comes from `protect_referenced_container_images`, which walks every container
on the host (running or stopped, any project) and protects whatever image it
references - it cannot drift out of sync with any product's naming, because it
never names anything.

## Install

**Mac Studio (launchd):**
```bash
bash agent-loops/docker-cleanup/install-launchd.sh
```
Runs daily at 03:30 local. See `install-launchd.sh` for the artifact-dir
defaults (every CI runner slot x product combination it can find).

Build cache defaults to a 24-hour age filter and a 10 GB storage budget, with
the installed buildx CLI's supported budget flag. The host context is explicitly
selected for this process (`DOCKER_CLEANUP_CONTEXT=default`), and its matching
builder is used unless `DOCKER_CLEANUP_BUILDER_NAME` overrides it. This leaves
the user's saved Docker context unchanged and prevents selected-context drift
from directing maintenance to CI or breaking Buildx pruning. Recent or in-use
cache can exceed the budget temporarily.
Release tags also require 24 hours since both image creation and local tagging
(`DOCKER_CLEANUP_IMAGE_MIN_AGE_HOURS`). A months-old upstream image can have been
pulled moments ago, so its build date alone does not prove it is stale. Missing
or invalid local tagging metadata retains the image. Age is refreshed immediately
before removal to protect tags reused after discovery. The age helper requires
Python 3; it reads inspection metadata through stdin and emits no image contents.
Stopped containers and all volumes are preserved by default; stopped-container
pruning requires `DOCKER_CLEANUP_PRUNE_STOPPED_CONTAINERS=1`.

**Linux host sharing a daemon with prod (e.g. the Hetzner standby):** there is
no launchd there, so this loop is deployed as a plain cron entry instead - see
`install-cron.sh`.

## Manual use

```bash
DRY_RUN=1 agent-loops/docker-cleanup/cleanup-local-docker.sh   # preview only
agent-loops/docker-cleanup/cleanup-local-docker.sh              # actually clean
```

## Maintenance opt-out

Registry-driven installs respect the standard opt-out convention: touch
`state/docker-cleanup/disabled` to skip a run without uninstalling.
