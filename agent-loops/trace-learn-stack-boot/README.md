# trace-learn-stack-boot

Brings the Trace Learn compose stacks (prod + staging) back up after a host reboot.

## Why

On 2026-07-26 the Mac Studio rebooted and the entire Trace Learn stack stayed down for
**~20 hours**. `api.tracelearn.app` served 502 the whole time while `tracelearn.app` kept
returning 200, because the frontend is Cloudflare Pages and `cloudflared` runs as its own
launchd agent. The app loaded and then failed every request, so nothing looked broken.

Two compounding causes:

1. **Docker Desktop on macOS starts at user LOGIN, not at boot.** Nothing runs until
   someone logs in.
2. The containers had exited `0` / `143` / `137` - graceful-stop codes - and
   `restart: unless-stopped` **deliberately does not restart an explicitly stopped
   container**. The policy worked as designed; the design was wrong for this host.

The compose files now use `restart: always`, which covers case 2. This agent covers case
1, which `restart: always` cannot: a restart policy is meaningless while the daemon is
down.

## What it does

`up.sh` waits for the Docker daemon (up to 5 min), then `docker start`s any existing
Trace Learn container that is not running, in dependency order:
`cloudsql-proxy -> otel-collector -> otel-healthcheck -> api -> worker`, for prod then
staging.

**It is not a deploy.** No build, no registry pull, no secrets, no migrations - it only
starts containers that already exist. If a stack was never deployed on this host there is
nothing to start, and that is not an error.

## Maintenance opt-out

Without this, deliberately stopping prod (backup, incident hold) would be silently undone
within 5 minutes.

```sh
touch <state-dir>/trace-learn-stack-boot/disabled   # hold everything down
rm    <state-dir>/trace-learn-stack-boot/disabled   # resume automatic bring-up
```

## Install (Mac Studio only)

```sh
bash agent-loops/trace-learn-stack-boot/install-launchd.sh
```

## Verified

- No-op when everything is already running (exit 0, single log line).
- Recovers **multiple** stopped containers in one pass - stopping staging `api` + `worker`
  and running it restored both. That case specifically catches a `set -e` bug an earlier
  draft had, where `[[ cond ]] && sleep` as the last statement of a `then` block exited
  the whole script and aborted bring-up after the first non-proxy container.
- Honours the disable flag in both directions.
- After each test, both environments returned HTTP 200.
