# dev-docker-cleanup

Weekly Docker janitor for the **MacBook Pro** (dev laptop). The Mac Studio has its own
`agent-loops/docker-cleanup`, built for a shared prod daemon; do not install that one here.

## What it removes

| Target | Rule |
|---|---|
| Dangling images | untagged layers no image tag or container references |
| Build cache | entries older than 7 days, keeping a 10 GB budget (`DEV_DOCKER_CLEANUP_BUILD_CACHE_MAX_AGE`, `DEV_DOCKER_CLEANUP_BUILD_CACHE_KEEP_STORAGE`) |
| Anonymous volumes | preserved by default; optional removal requires `DEV_DOCKER_CLEANUP_PRUNE_ANONYMOUS_VOLUMES=1`, unattached status and age over 30 days |

## What it never touches

- Tagged images, even unused ones. Pull cost is cheap; a surprise re-pull mid-task is not.
- Containers, running or stopped.
- **All volumes** by default, including anonymous and named volumes, even when unattached. A named volume left behind by
  `docker compose down` is usually a dev database someone expects to survive. Unattached
  named volumes are listed in the run log for a human to delete by hand.

If Docker Desktop is not running the script exits 0 with one log line.

## Commands

```sh
agent-loops/dev-docker-cleanup/cleanup-dev-docker.sh --status     # docker system df + last run
agent-loops/dev-docker-cleanup/cleanup-dev-docker.sh --dry-run    # what would go
agent-loops/dev-docker-cleanup/cleanup-dev-docker.sh              # clean
```

## Install (MacBook Pro only)

```sh
bash agent-loops/dev-docker-cleanup/install-launchd.sh
```

Installs `com.jud.dev-docker-cleanup`, Monday 09:30 local via `StartCalendarInterval`.
launchd fires a missed calendar slot on the next wake, so a closed lid shifts the run
rather than skipping it. Run on demand with:

```sh
launchctl kickstart gui/$(id -u)/com.jud.dev-docker-cleanup
```

State and logs: `state/dev-docker-cleanup/` (gitignored).
