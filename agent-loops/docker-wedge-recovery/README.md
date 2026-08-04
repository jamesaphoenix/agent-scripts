# Docker wedge detector and recovery

Detects the Mac Studio's Docker Desktop "wedge" and recovers it by rebooting
the host.

## What it detects

Docker Desktop periodically enters a state where the daemon looks healthy from
every cheap angle - `docker ps`, `docker exec` and `docker pull` all answer,
and the socket still returns HTTP 200 on `/_ping` - but **every new container
start hangs forever** and containers pile up in `Created`. A whole-VM `sync`
hangs too. Measured on 2026-08-04.

So the probe actually starts a container:

```bash
docker run --rm alpine:3.20 true    # bounded at 60s
```

Nothing that only reads daemon state can see this wedge.

Before acting, the script confirms the signature: the container start **hung**
(not failed fast) while the socket `_ping` and/or `docker ps` still answer. A
daemon that answers nothing is treated as down/starting - Docker Desktop starts
at user *login* on macOS - and is never a reboot trigger.

Two things that lie in this state and are therefore never used:
`docker desktop status` reports "running" with no socket at all, and
`docker desktop stop` / `start` hang. The socket is checked directly with
`curl --unix-socket`.

## Why reboot, and why it never purges

The root cause is accumulated **host-side** Docker Desktop state on the macOS
side - not the Linux VM, not the hypervisor, not the workload. A host reboot is
the only recovery proven to work.

Purging the VM disk (`~/Library/Containers/com.docker.docker/Data/vms`) was
tried first during the incident: it bought about 40 minutes and destroyed 218GB
of images and volumes for nothing. **This loop must never purge the VM.**

No `pkill` or `killall` anywhere either: a repo hook blocks unscoped kills
targeting Docker, and force-killing Docker processes made the incident worse.
Commands are bounded by backgrounding them and polling `ps -p <pid>`
(`lib/bounded.sh`); on expiry only our own direct child pid gets a plain
SIGTERM.

Rebooting is safe unattended on the Studio: FileVault is Off, auto-login is on,
Docker Desktop AutoStart brings the daemon back and `restart: always` brings the
stacks back. Reboot is issued as `sudo -n /sbin/shutdown -r now`, permitted by
`/etc/sudoers.d/reboot-nopasswd`. macOS `shutdown` has no `-c` cancel flag and
`shutdown -h +N` runs in the foreground, so only the immediate form is used.

## Host guard

Reboot only ever fires when **both** the current user and one of the host's
names match the allowlist (`jamesphoenix` on `Jamess-Mac-Studio` /
`Jamess-Mac-Studio.local` / `JamessMacStudio.fritz.box`). Names are collected
from `hostname`, `hostname -s`, `scutil --get LocalHostName` and
`scutil --get ComputerName`, plus `.local`-stripped variants, and compared
case-insensitively. Anywhere else - the MacBook, a worktree, CI - the script
still probes and reports, then logs `WOULD REBOOT` and stops.

## Rate limits

Never more than once per `DOCKER_WEDGE_MIN_REBOOT_INTERVAL_MINUTES` (default 60)
and never more than `DOCKER_WEDGE_MAX_REBOOTS_PER_DAY` (default 3) in a rolling
24 hours. When the cap is hit it logs loudly and does **not** reboot, so a
machine that is broken in some other way cannot be put into a boot loop.

## Post-boot verification

After a reboot this loop triggered, the next healthy run verifies the socket
answers `_ping` and that the expected stacks
(`octospark-prod-live`, `octospark-staging-live` by default) are running, then
writes `last-post-boot-verification.json`.

## State

Everything lives under `state/docker-wedge-recovery/` (gitignored):

| File | Meaning |
|------|---------|
| `last-status.json` | Result of the most recent run |
| `last-reboot.json` | Reason + timestamp of the last reboot this loop issued |
| `reboot-history` | Epoch seconds per reboot, pruned to 24h - backs the rate limits |
| `pending-verification` | Present between a reboot and its post-boot verification |
| `last-post-boot-verification.json` | Socket + expected-stack state after the last reboot |
| `disabled` | Maintenance opt-out |
| `logs/` | launchd stdout/stderr |

## Install (Mac Studio only)

Through the central registry:

```bash
scripts/install-launchd-tasks.sh --task host-docker-wedge-recovery
```

## Manual use

```bash
# Probe and report, never reboots (safe anywhere, including the MacBook):
DOCKER_WEDGE_ALLOW_REBOOT_HOSTS=none agent-loops/docker-wedge-recovery/check.sh

# Real behaviour, on the Studio:
agent-loops/docker-wedge-recovery/check.sh
```

## Maintenance opt-out

```bash
touch state/docker-wedge-recovery/disabled   # skip every run
rm    state/docker-wedge-recovery/disabled   # resume
```
