# Proposed /etc/docker/daemon.json for the shared daemon (PROPOSAL, not applied)

The host has no `/etc/docker/daemon.json`, so the daemon runs on defaults: live-restore
is off, and container logs are unbounded unless a compose file sets them (the
tunnels, both BuildKit builders and the CI containers have `{}` today).

```json
{
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "default-address-pools": [{ "base": "172.28.0.0/14", "size": 24 }]
}
```

- `live-restore`: containers keep running while `dockerd` is stopped or restarted
  (package upgrades, config changes). Production must not blink for a daemon restart.
- `log-opts`: caps each container at 30 MiB of logs by default. This matches what the
  staging compose files already set explicitly.
- `default-address-pools`: new networks take /24s from `172.28.0.0/14` (1024 networks).
  The default pool hands out /16s from `172.17.0.0/12` and the host already uses
  `172.17` to `172.24` plus a LAN on `192.168.178.0/24`; with two prod projects, more
  staging projects and per-job networks the /16 pool runs out quickly. Existing
  networks are unchanged and do not overlap the new range. Drop this key if
  `172.28.0.0/14` clashes with anything on your Tailscale routes or VPNs.
- Not set on purpose: `data-root` (stays `/var/lib/docker`), `iptables`,
  `userland-proxy`, `storage-driver`, `exec-opts` (the systemd cgroup driver is already in
  use). The rootless CI daemon has its own `daemon.json` (see `../rootless/`).

## Effect of live-restore on running containers

- **Enabling it does not restart anything if you use a reload.** `live-restore` is one of
  the options the daemon re-reads on `SIGHUP`, so `sudo systemctl reload docker` turns it
  on without stopping a single container. Confirm with
  `docker info --format '{{.LiveRestoreEnabled}}'` (expect `true`). The other keys only
  take effect for new objects (new containers for `log-opts`, new networks for the
  pools; `default-address-pools` and `log-driver` need a real restart to load).
- **Do not run `systemctl restart docker` first.** With live-restore still off, a restart
  stops every container; those with `restart: always` / `unless-stopped` come back, but
  the CI containers (`restart: no`) and `naughty_neumann` stay down and in-flight CI jobs
  fail. After live-restore is on, a restart leaves containers running.
- While the daemon is down under live-restore: running containers and their published
  ports keep working, but there are no `docker` commands, no health-check results, no log
  collection by the daemon (json-file logs written by the shim resume), restart policies
  are not enforced and nothing new can be started. Keep that window short.
- Limits: not supported with Swarm (it is inactive here); changing the storage driver or
  some network options across a restart needs containers recreated; a major-version
  daemon upgrade can still require a restart of containers.
- `log-opts` do **not** retroactively apply: existing containers keep the logging config
  they were created with. Recreate the tunnels, builders and CI containers to bound them
  (the rootless migration recreates the CI ones).

## Apply

```sh
sudo install -d -m 755 /etc/docker
sudo install -m 644 daemon.json /etc/docker/daemon.json
sudo dockerd --validate --config-file /etc/docker/daemon.json     # syntax and option check
sudo systemctl reload docker                                      # enables live-restore, no restarts
docker info --format 'live-restore={{.LiveRestoreEnabled}}'       # must print true
docker ps --format '{{.Names}} {{.Status}}' | head                # nothing restarted
```

Later, in a quiet moment, one deliberate `sudo systemctl restart docker` loads the
remaining keys while containers keep running. Check `docker network create probe &&
docker network inspect probe --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`
(expect `172.28.x.0/24`) and `docker network rm probe`.

## Rollback

`sudo rm /etc/docker/daemon.json && sudo systemctl reload docker` returns the reloadable
options to defaults (live-restore off again; a later restart then stops containers
again, so do not restart with it removed unless you intend that). Networks created from
the new pool keep their subnets until removed.

Tests: `python3 -m unittest discover -s tests -p 'test_a_series_daemon.py'`.
