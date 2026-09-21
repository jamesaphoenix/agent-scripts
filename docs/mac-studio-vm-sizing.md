# Mac Studio VM sizing

The Studio is an M4 Max / 128GB running two Docker daemons plus four CI
runners. Both VMs were originally allocated far more memory than their
workloads use, and an Apple Virtualization VM grows to fill its allocation and
keeps those pages resident on the host.

## Change of 2026-09-08

| VM | Allocated before | Allocated after | Actual container usage |
|---|---|---|---|
| Docker Desktop | 50 GiB (`MemoryMiB` 51200) | 24 GiB (24576) | ~13 GB |
| Colima `ci` | 24 GB | 16 GB | ~8 GB |

Measured effect on the host:

```
VM resident   77.9 GB  ->  32.6 GB
free memory    3.3 GB  ->  51.6 GB
compressor    14.7 GB  ->   0.9 GB
swap used      1.5 GB  ->  0.47 GB
```

The compressor figure is the one that matters. At 14.7GB the machine was
continuously compressing pages to stay afloat; at 0.9GB it is not under
pressure at all. That headroom is what makes local inference possible
alongside the production stacks.

Sizing rule used: roughly 1.8x steady-state container usage. Both VMs keep
ample headroom (Docker Desktop ~10GB spare, Colima ~9GB spare) while no longer
hoarding tens of gigabytes of host RAM they never touch.

## Where the settings live

- **Docker Desktop:** `MemoryMiB` in
  `~/Library/Group Containers/group.com.docker/settings-store.json`.
  Stop the engine before editing (`docker desktop stop`), or Docker Desktop
  rewrites the file on quit and the edit is lost. Then `docker desktop start`.
- **Colima `ci`:** `memory:` in `~/.colima/ci/colima.yaml`, then
  `colima stop ci && colima start ci`. Park the `com.jud.colima-ci` keepalive
  first (`launchctl bootout gui/501/com.jud.colima-ci`) so it cannot race the
  restart, and bootstrap it back afterwards.

## Restart consequences (both are production events)

Docker Desktop restarting takes down every production and staging stack behind
the cloudflared tunnels. Recovery is not automatic for all of it:

- 22 containers are `restart: always` and return on their own.
- 16 containers (the whole `tx-agent-kit` observability stack) are
  `restart: no` and come back only via `com.jud.ensure-dockerised-apps-online`,
  which polls every 5 minutes. Kickstart it to avoid the wait:
  `launchctl kickstart -k gui/501/com.jud.ensure-dockerised-apps-online`.

The Colima `ci` VM's 24 containers are **all** `restart: no` and none return by
themselves. Nothing polls for them either, because
`ensure-dockerised-apps-online` only covers the host daemon. Start the
datastores (postgres, clickhouse, redis, minio) first, wait, then the rest.

## Verifying afterwards

```bash
docker ps -q | wc -l                                    # expect 38
docker ps --filter health=unhealthy --format '{{.Names}}'   # expect empty
for h in api.octospark.ai mcp.octospark.ai api.tracelearn.app \
         api-staging.tracelearn.app api-staging.octospark.ai; do
  curl -s -o /dev/null -w "$h %{http_code}\n" "https://$h/health"
done
```

All five returned 200 after the 2026-09-08 change.

## Reverting

Settings backups are written to `~/right-size-backup-<timestamp>/` on the
Studio, containing the original `settings-store.json` and `colima-ci.yaml`.
Restore the file, restart the relevant VM, and recover containers as above.
