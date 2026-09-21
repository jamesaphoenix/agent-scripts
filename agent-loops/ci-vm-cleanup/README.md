# CI VM janitor

Reclaims disk inside the Colima `ci` VM and returns it to the host, and reaps
orphaned ephemeral CI containers on the host Docker daemon.

## Why this is separate from `docker-cleanup`

The Mac Studio runs **two** Docker daemons:

| Daemon | Used by | Cleaned by |
|---|---|---|
| Docker Desktop (host) | prod + staging compose stacks | `docker-cleanup` (03:30) |
| Colima `ci` VM | self-hosted GitHub Actions runners | **this loop** (04:10) |

`docker-cleanup` only ever talks to the host daemon. Nothing cleaned the CI
VM, and by 2026-09-08 it held 398 orphaned anonymous volumes (36GB) while its
sparse datadisk had grown to 108GB on the host, helping push the boot volume
to 98% full.

## The fstrim step is the point

Pruning inside the guest frees space in the guest filesystem only. The host's
`datadisk` is a raw sparse image that **only ever grows** - freed guest blocks
stay allocated on macOS until the guest issues TRIM. On 2026-09-08 the prune
freed 34GB inside the VM; the subsequent `fstrim` took the host file from
108GB to 27GB. Skip the trim and the reclaim is invisible to the host.

## What it will not touch

- **Named volumes.** `docker volume prune` runs without `--all`, so the
  long-lived CI service containers (postgres, clickhouse, minio, langfuse,
  redis) keep their data.
- **Running containers**, in the VM or on the host.
- **The base image cache.** Image pruning is dangling-only; a full
  `image prune -a` would just convert disk cost into pull time on every run.
- **Any container younger than `CI_VM_CLEANUP_ORPHAN_MIN_AGE_HOURS`** (3h),
  or whose creating shell PID is still alive.

## The orphan leak it guards against

`trace-learn/scripts/content-generation/mobile-readiness/run-environment.sh`
starts a detached `cloud-sql-proxy` container and removes it from a shell
`EXIT` trap. When a workflow is cancelled or times out the shell is
SIGKILLed, the trap never runs, and the container is left holding an
authenticated tunnel to the production Cloud SQL instance open indefinitely.
Eight had accumulated over seven days, the oldest running for a week.

The script itself was fixed to label its containers, but the reaper stays as
defence in depth: any script that spawns a detached container has this
failure mode.

## Install

```bash
bash agent-loops/ci-vm-cleanup/install-launchd.sh
```

Runs daily at 04:10 local, after the 03:30 host janitor.

## Manual use

```bash
DRY_RUN=1 agent-loops/ci-vm-cleanup/cleanup-ci-vm.sh   # preview only
agent-loops/ci-vm-cleanup/cleanup-ci-vm.sh             # actually clean
```

## Maintenance opt-out

Touch `state/ci-vm-cleanup/disabled` to skip a run without uninstalling.
