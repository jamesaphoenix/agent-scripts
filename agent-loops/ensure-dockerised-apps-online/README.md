# ensure-dockerised-apps-online

Keeps every Docker Compose app listed in `config/docker-apps.json` online on the Mac Studio.
Formerly `trace-learn-stack-boot`, which only knew about Trace Learn.

## Why

On 2026-07-26 the Mac Studio rebooted and the entire Trace Learn stack stayed down for
**~20 hours**. `api.tracelearn.app` served 502 the whole time while `tracelearn.app` kept
returning 200, because the frontend is Cloudflare Pages and `cloudflared` runs as its own
launchd agent. The app loaded and then failed every request, so nothing looked broken.

Two compounding causes:

1. **Docker Desktop on macOS starts at user LOGIN, not at boot.** Nothing runs until
   someone logs in, and a container restart policy is meaningless while the daemon is down.
2. The containers had exited `0` / `143` / `137` - graceful-stop codes - and
   `restart: unless-stopped` **deliberately does not restart an explicitly stopped
   container**. The compose files now use `restart: always`, which covers this case for
   Octospark and Trace Learn. `tx-agent-kit` still runs with `restart: no`, so only this loop
   brings it back.

## What it does

`ensure-online.sh` waits for the Docker daemon (up to 5 min), then for each enabled app in
`config/docker-apps.json` runs `docker start` on any existing container that is not running,
in the app's `startOrder`, sleeping `startDelaySeconds` after a dependency such as
`cloudsql-proxy` or `redis` comes up. Containers are found by compose labels
(`com.docker.compose.project` + `com.docker.compose.service`), never by hardcoded names.

**It is not a deploy.** No build, no registry pull, no secrets, no migrations. If an app was
never deployed on this host there is nothing to start, and that is not an error.

## Adding an app

Append an entry to `config/docker-apps.json`:

```json
{
  "id": "my-app-prod",
  "composeProject": "my-app-prod",
  "enabled": true,
  "startOrder": ["postgres", "redis", "api", "worker"],
  "startDelaySeconds": { "postgres": 5 },
  "temporal": { "enabled": false }
}
```

`composeProject` is the value of the `com.docker.compose.project` label (`docker compose -p`).
Validate, deploy, and the next run picks it up:

```sh
python3 lib/docker-apps.py validate
scripts/deploy-to-mac-studio.sh --live
```

Set `temporal.enabled` to `true` (with `checkFromService` etc.) if the app's workers talk to the
Hetzner Temporal server; that is consumed by `agent-loops/temporal-worker-tailscale-health`.

## Maintenance opt-out

Without this, deliberately stopping prod (backup, incident hold) would be silently undone
within 5 minutes.

```sh
touch <state-dir>/disabled              # hold every app down
touch <state-dir>/disabled.<app-id>     # hold one app down, e.g. disabled.trace-learn-prod
rm    <state-dir>/disabled              # resume
```

`<state-dir>` is `state/ensure-dockerised-apps-online/` under the runtime agent-scripts copy.

## Commands

```sh
agent-loops/ensure-dockerised-apps-online/ensure-online.sh --status     # container state per app
agent-loops/ensure-dockerised-apps-online/ensure-online.sh --dry-run    # what would be started
agent-loops/ensure-dockerised-apps-online/ensure-online.sh              # start stopped containers
agent-loops/ensure-dockerised-apps-online/ensure-online.sh --app tx-agent-kit
```

## Install (Mac Studio only)

```sh
scripts/install-launchd-tasks.sh --task ensure-dockerised-apps-online
```

The installer boots out and removes the legacy `com.jud.trace-learn-stack-boot` agent.
Runs every 5 minutes plus at login as `com.jud.ensure-dockerised-apps-online`.
