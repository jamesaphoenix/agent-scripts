# Rootless Docker for the CI runners

**Status: proposal, not applied.** `setup-ci-rootless-docker.sh` has never been run on
the host. Review it, run `--check` and `--dry-run` first.

## Why

`ci-runner` is in the `docker` group, which is root on the host, and CI shares one
daemon with the staging stacks and, soon, production. A CI job (or a malicious PR on an
allowed repository) can read production secrets, stop containers or prune images. With
rootless Docker, CI gets its own daemon, data-root and socket, owned by an unprivileged
user, and the group membership is removed.

| | shared daemon (production, staging, observability) | CI daemon (new) |
|---|---|---|
| owner | root | `ci-runner` (uid 1001) |
| socket | `/var/run/docker.sock` | `/run/user/1001/docker.sock` |
| data-root | `/var/lib/docker` | `/home/ci-runner/.local/share/docker` |
| published ports | explicit per compose file | default `127.0.0.1` (daemon `"ip"`) |

## What the script does (phases, in order; each is idempotent)

1. `install`: apt packages `uidmap rootlesskit slirp4netns iptables dbus-user-session`;
   checks the AppArmor profile for rootlesskit (Ubuntu restricts unprivileged user
   namespaces); writes `/etc/systemd/system/user@.service.d/jud-delegate.conf`
   (`Delegate=cpu cpuset io memory pids`); `loginctl enable-linger ci-runner`; writes
   `~ci-runner/.config/docker/daemon.json` (data-root, systemd cgroup driver, `"ip":
   "127.0.0.1"`, log rotation); runs Docker's `dockerd-rootless-setuptool.sh install` from
   `/usr/share/docker.io/contrib` as ci-runner; enables the `docker.service` user unit.
2. `runners`: renders the four `jud-actions-runner-N.service` units from
   `runner.service.template`, which now sets `Environment=DOCKER_HOST=unix:///run/user/1001/docker.sock`
   and drops `SupplementaryGroups=docker` and `Requires=docker.service`; points the two CI
   cleanup units at the same socket. Restarts only with `--restart-runners`.
3. `builders`: creates `octospark-cache-builder` and `trace-learn-cache-builder` on the
   rootless daemon (docker-container driver, `network.host` entitlement as today),
   keeping the containers but not the buildx metadata (jobs register their own).
4. `services`: for `octospark-ci` and `trace-learn-ci`, copies the locally built images
   across (`docker save | docker load`), stops (does not remove) the old containers so
   ports 15432, 15433, 16379 and 16380 are free, and runs `docker compose up -d --wait`
   from the same workspace compose files on the rootless daemon. Data is **not**
   migrated: these databases run with `fsync=off` and are rebuilt by the jobs.
5. `group`: only if the rootless socket, both builders and the runner template are in
   place: `gpasswd -d ci-runner docker`.

Phases 2 to 5 refuse to run while any `Runner.Worker` is active.

## Apply

```sh
sudo agent-loops/a-series-ci/rootless/setup-ci-rootless-docker.sh --check
sudo agent-loops/a-series-ci/rootless/setup-ci-rootless-docker.sh --dry-run
sudo agent-loops/a-series-ci/rootless/setup-ci-rootless-docker.sh --phase install
runuser -u ci-runner -- env XDG_RUNTIME_DIR=/run/user/1001 DOCKER_HOST=unix:///run/user/1001/docker.sock docker info
# wait for idle runners (GitHub: no job in progress), then:
sudo agent-loops/a-series-ci/rootless/setup-ci-rootless-docker.sh --phase runners --phase builders --phase services --restart-runners
# run one real CI job on a runner, check it passes, then:
sudo agent-loops/a-series-ci/rootless/setup-ci-rootless-docker.sh --phase group
sudo agent-loops/a-series-ci/rootless/setup-ci-rootless-docker.sh --check   # exit 0 when complete
```

Then `provision-runners.sh` (which no longer adds the docker group and refuses to run
without the rootless socket) and `install-runtime.sh` use the same final state.
Runners 3 and 4 are currently in `failed`: after the restart, `systemctl status
jud-actions-runner-3` tells you whether the cause was the shared daemon or something else.

## Rollback

Each phase is reversible and the shared daemon is never modified.

- Group: `sudo usermod -aG docker ci-runner` (runner restart needed).
- Runners: restore the previous unit text from git (`runner.service.template` history),
  re-render the four units, `systemctl daemon-reload`, restart idle runners. CI is then
  on the shared daemon again.
- Services: `docker stop` the rootless project containers
  (`runuser -u ci-runner -- env DOCKER_HOST=unix:///run/user/1001/docker.sock docker compose -p octospark-ci stop`)
  and `docker start` the old ones on the shared daemon; they were only stopped.
- Whole daemon: `runuser -u ci-runner -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user disable --now docker.service`,
  `loginctl disable-linger ci-runner`, then optionally `rm -rf /home/ci-runner/.local/share/docker`
  (this is where the CI images and cache live, so only after the rollback is verified).
  Remove `/etc/systemd/system/user@.service.d/jud-delegate.conf` if nothing else needs it.

### Cleaning up the old CI containers (deliberate, afterwards)

The script never deletes anything on the shared daemon. Once CI has been green on the
rootless daemon for a few days, remove exactly the old objects:
`docker rm octospark-ci-postgres-1 octospark-ci-redis-1 trace-learn-ci-postgres-1 trace-learn-ci-redis-1`,
`docker rm -f buildx_buildkit_octospark-cache-builder0 buildx_buildkit_trace-learn-cache-builder0`, and their
`*_state` and `*-ci_*_data` volumes by name (never `volume prune`). The ~800 GB of `ci-*`
images (`octospark-renderer`, `octospark-media-runtime`) and the build cache on the shared
daemon are then unreferenced: reclaim them with
`jud-image-retention.py --report` first, then targeted `docker image rm` after review.

## Known rootless limits

- **Ports below 1024** cannot be bound by default. Set
  `net.ipv4.ip_unprivileged_port_start=0` (host-wide, so weigh it) or avoid privileged
  ports in CI compose files. CI uses 4100 to 4302, 15432 to 16380 only.
- **Resource limits** (`--memory`, `--cpus`, `--pids-limit`) work only with cgroup v2,
  the systemd cgroup driver and delegation. The script installs `Delegate=` for
  `user@.service`; check `cat /sys/fs/cgroup/user.slice/user-1001.slice/user@1001.service/cgroup.controllers`.
- **Storage**: native `overlay2` works in a user namespace on kernel 5.11 and later (this
  host runs 7.0) on ext4/xfs; no `fuse-overlayfs` is installed. The data-root is on the
  same root disk, so the 86% full disk is shared; size the CI cache accordingly.
- **Networking** goes through rootlesskit with slirp4netns: lower throughput, the source
  IP of inbound connections is not preserved, and containers reach the host at `10.0.2.2`
  (`host.docker.internal` needs `--add-host`). `ping` and some raw sockets need
  `net.ipv4.ping_group_range` to include the user.
- **Testcontainers**: ryuk mounts the Docker socket by path. Set
  `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock` in jobs that use it, because
  inside the rootless daemon that path is the in-namespace socket.
- **Privileged containers** are privileged only inside the user namespace (enough for
  BuildKit's docker-container driver, not for mounting host devices or `sysctl`).
- A second daemon uses its own memory (dockerd, containerd, rootlesskit); budget about
  0.3 GiB. Place it under `jud-ci.slice` using the `Slice=` drop-in in `../slices/README.md`.
