# CI VM janitor

Reclaims disk inside the Colima `ci` VM and returns it to the host, and reaps
orphaned ephemeral CI containers on the host Docker daemon. The nightly job
also removes stale per-commit renderer and media-runtime images.

## Why this is separate from `docker-cleanup`

The Mac Studio runs **two** Docker daemons:

| Daemon | Used by | Cleaned by |
|---|---|---|
| Docker Desktop (host) | prod + staging compose stacks | `docker-cleanup` (03:30) |
| Colima `ci` VM | self-hosted GitHub Actions runners | **this loop** (04:10) |

The host `docker-cleanup` job targets Docker Desktop. This CI loop invokes
the same janitor with an explicit `colima-ci` context and matching builder.
Before this loop existed, nothing cleaned the CI VM, and by 2026-09-08 it held 398 orphaned anonymous volumes (36GB) while its
sparse datadisk had grown to 108GB on the host, helping push the boot volume
to 98% full.

## The fstrim step is the point

Pruning inside the guest frees space in the guest filesystem only. The host's
`datadisk` is a raw sparse image that **only ever grows** - freed guest blocks
stay allocated on macOS until the guest issues TRIM. On 2026-09-08 the prune
freed 34GB inside the VM; the subsequent `fstrim` took the host file from
108GB to 27GB. Skip the trim and the reclaim is invisible to the host.

Allocation measurements locate named profiles under `colima-<profile>` and
the default profile under `colima`, respecting `COLIMA_HOME` and retaining an
unprefixed-layout fallback. The bytes printed by `fstrim` are potential discard
bytes; repeated trims can report the same ranges. Compare the host's allocated
datadisk size before and after to measure actual sparse-file reduction.

## What it will not touch

- **All volumes by default.** Anonymous-volume pruning requires explicit
  `CI_VM_CLEANUP_PRUNE_ANONYMOUS_VOLUMES=1`. Named volumes are always preserved.
- **Running containers**, in the VM or on the host.
- **Tagged base images.** Only `octospark-renderer` and
  `octospark-media-runtime` repositories are eligible for tagged-image cleanup.
- **Recent renderer/runtime images.** Both creation and local tagging timestamps
  must be at least 24 hours old. Missing timestamps retain the image.
- **Images referenced by any CI container, running or stopped.** The shared
  janitor inventories references before mutation and aborts if inventory fails.
  It rechecks image age immediately before removal and never forces removal.
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
bash scripts/install-launchd-tasks.sh --task ci-vm-cleanup
```

Install on Mac Studio only. Runs daily at 04:10 local, after the 03:30 host
janitor. The `com.jud.ci-vm-cleanup` LaunchAgent writes status and logs under
`state/ci-vm-cleanup/`; it needs no credentials.

## Manual use

```bash
DRY_RUN=1 agent-loops/ci-vm-cleanup/cleanup-ci-vm.sh   # preview only
agent-loops/ci-vm-cleanup/cleanup-ci-vm.sh             # actually clean
```

## Maintenance opt-out

Touch `state/ci-vm-cleanup/disabled` to skip a run without uninstalling.

Build cache defaults to a 24-hour age filter and a 10 GB storage budget. The
installed buildx CLI is probed for its supported budget flag. Cache-prune
failures fail the job. Successful cleanup runs guest trim to return reclaimed
blocks to macOS. Dry runs perform inventory and print proposed mutations
without deleting images or issuing TRIM.
