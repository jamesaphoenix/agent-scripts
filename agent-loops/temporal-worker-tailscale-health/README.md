# temporal-worker-tailscale-health

Checks that every Temporal-backed app in `config/docker-apps.json` (Octospark prod/staging,
Trace Learn prod/staging today) can reach the Hetzner Temporal server over Tailscale, and
repairs the route when it goes stale. Formerly `octospark-temporal-route-health`, which only
checked Octospark staging.

Runs every **minute** on the Mac Studio as `com.jud.temporal-worker-tailscale-health`.

## Why

Tailscale on macOS periodically leaves a stale route: `tailscale ping` still succeeds but a
normal TCP connection does not, and every worker on the host silently stops polling its task
queue. This intentionally does not use `tailscale ping` for that reason.

## What it checks

For each app whose `temporal.enabled` is true:

1. Finds the running `checkFromService` container (default `api`) by compose labels.
2. Reads `TEMPORAL_ADDRESS` from that container's environment, so the check always targets
   the address the app was actually started with (fallback: `defaults.temporalAddress`).
3. Opens a TCP connection to Temporal **from inside the container**.

Then, once per distinct target address, on the host:

4. Confirms the macOS routing table sends the Tailscale IP through a `utun` interface.
5. Opens a plain TCP connection from the host.

Apps with no running container are recorded as `skipped`, not failed - bringing them up is
`ensure-dockerised-apps-online`'s job.

## Repair

On any failure: `tailscale down`, `tailscale up`, re-check. If the re-check passes it restarts
every app's `restartServicesAfterRepair` containers and kickstarts every
`kickstartLaunchdAfterRepair` launchd label (the Octospark auto-fix runners are plain launchd
Node processes, not compose services, and hold a dead gRPC connection after a flap).

Repairs are rate-limited to one per 10 minutes (`TEMPORAL_HEALTH_REPAIR_COOLDOWN_SECONDS`) so a
genuine Temporal outage cannot flap the tunnel every minute. Each run is also killed after
170 s so a wedged Docker daemon cannot hang the loop.

## Adding an app

Give its entry in `config/docker-apps.json` a `temporal` block:

```json
"temporal": {
  "enabled": true,
  "checkFromService": "api",
  "restartServicesAfterRepair": ["worker"],
  "kickstartLaunchdAfterRepair": []
}
```

Validate with `python3 lib/docker-apps.py validate`, deploy, done.

## Commands

```sh
agent-loops/temporal-worker-tailscale-health/check.sh --status       # last recorded result
agent-loops/temporal-worker-tailscale-health/check.sh --no-repair    # check only
agent-loops/temporal-worker-tailscale-health/check.sh                # check and repair
```

State: `state/temporal-worker-tailscale-health/last-status.json` (per-app results),
`last-route.txt`, `last-repair-at`.

## Install (Mac Studio only)

```sh
scripts/install-launchd-tasks.sh --task temporal-worker-tailscale-health
```

The installer boots out and removes the legacy `com.octospark.temporal-route-health` agent.
