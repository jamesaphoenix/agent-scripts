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
