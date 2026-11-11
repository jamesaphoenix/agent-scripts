# systemd slices for the shared host (PROPOSAL, to confirm)

**This is a proposal. Nothing here has been installed and the numbers have not been
agreed.** Confirm the budget before installing; adjust the unit files, not the idea.

The host has 59 GiB RAM, 24 cores, 7 GiB swap (currently full), cgroup v2 and Docker with
the systemd cgroup driver. Today every container and CI job competes in `system.slice`,
so one heavy CI build can evict production. Slices give each class of workload a memory
ceiling and a CPU share.

## Proposed budget

| Slice | MemoryHigh | MemoryMax | CPUWeight | IOWeight | What goes in it |
|---|---|---|---|---|---|
| `jud-prod.slice` | 18 GiB | 20 GiB | 500 | 500 | `trace-learn-prod`, `octospark-prod` (swap disabled) |
| `jud-data.slice` | 3.5 GiB | 4 GiB | 400 | 400 | `*-staging-data` databases, later prod data (swap disabled) |
| `jud-staging.slice` | 7 GiB | 8 GiB | 200 | 200 | `octospark-staging`, `trace-learn-staging`, tunnels |
| `jud-observability.slice` | 896 MiB | 1 GiB | 200 | 100 | `jud-observability` (collector, container limit 768m) |
| `jud-ci.slice` | 16 GiB | 18 GiB | 50 | 50 | runner services, their jobs, the rootless CI daemon |
| **Allocated (MemoryMax)** | | **51 GiB** | | | |
| **Unallocated** | | **8 GiB** | | | kernel, page cache, `system.slice`, Tailscale, headroom |

`MemoryHigh` throttles and reclaims before `MemoryMax` invokes the OOM killer, so a slice
slows down before anything dies. CPUWeight is relative (default 100) and only matters
under contention: CI has the lowest weight, so it yields to everything else but can use
idle cores. `MemorySwapMax=0` keeps prod and data off the swap (which is full today).
Open questions: whether 4 GiB is enough once a production database lands in
`jud-data.slice` next to two staging databases, and whether CI (24 cores, parallel Turbo)
tolerates 16 GiB; both are easy to change later (`systemctl set-property` is live).

## Apply

```sh
sudo install -m 644 slices/*.slice /etc/systemd/system/
sudo systemctl daemon-reload
systemctl cat jud-prod.slice                       # check
systemd-analyze verify /etc/systemd/system/jud-*.slice
sudo systemctl start jud-prod.slice jud-data.slice jud-staging.slice jud-observability.slice jud-ci.slice
systemctl status jud-ci.slice                      # Memory: ... (high: 16.0G max: 18.0G)
```

Slices on their own change nothing; workloads must be attached.

## Attach compose projects (cgroup_parent)

With cgroup v2 and `native.cgroupdriver=systemd` (this host: `docker info` says
`systemd`), set `cgroup_parent` to the slice name per service in the compose file (or in
a `compose.slices.yaml` override, merged with `-f`):

```yaml
services:
  api:
    cgroup_parent: jud-prod.slice
  worker:
    cgroup_parent: jud-prod.slice
  cloudsql-proxy:
    cgroup_parent: jud-prod.slice
```

Mapping: `octospark-prod`, `trace-learn-prod` to `jud-prod.slice`; `octospark-staging`,
`trace-learn-staging` and the two `*-tunnel` containers to `jud-staging.slice`;
`octospark-staging-data`, `trace-learn-staging-data` (and later prod databases) to
`jud-data.slice`; `jud-observability` to `jud-observability.slice` (the `minisforum`
profile in `host-observability` already sets this). `cgroup_parent` cannot be changed on a
running container: recreate it (`docker compose up -d --force-recreate <service>`), one
service at a time, starting with staging. Verify with:

```sh
docker inspect --format '{{.HostConfig.CgroupParent}}' octospark-staging-api-1
systemd-cgls -u jud-staging.slice
```

Compose files that omit `cgroup_parent` stay in `system.slice` (unlimited): the port
check (`../ports.json`) is not a substitute, so also add a review check that every
production service sets it.

## Attach the runners (Slice=)

`runner.service.template` now has `Slice=jud-ci.slice`, so the four runner services and
every job they spawn (`KillMode=process` keeps its current behaviour) are accounted to
it. Re-render the units (see `rootless/README.md`, phase `runners`), `daemon-reload`, restart
idle runners, then `systemd-cgls -u jud-ci.slice`.

The rootless CI daemon runs under `user@1001.service`. To account its containers to CI
as well, add (untested, verify with `systemd-cgls` after a restart of the user manager):

```ini
# /etc/systemd/system/user@1001.service.d/jud-slice.conf
[Service]
Slice=jud-ci.slice
```

Containers of the rootless daemon can only use controllers delegated to the user manager
(`rootless/` installs `Delegate=cpu cpuset io memory pids`).

## Rollback

`sudo rm /etc/systemd/system/jud-*.slice /etc/systemd/system/user@1001.service.d/jud-slice.conf && sudo systemctl daemon-reload`
removes the limits (running scopes fall back to their parent on recreate). To relax live
without editing: `sudo systemctl set-property --runtime jud-ci.slice MemoryMax=24G`.
Remove `cgroup_parent` lines and recreate the containers to return them to `system.slice`.

Tests: `python3 -m unittest discover -s tests -p 'test_a_series_slices.py'`.
