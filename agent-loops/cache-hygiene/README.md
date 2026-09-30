# Cache hygiene

Cache-only maintenance authored on the MacBook and scheduled hourly on the Mac Studio.
Manual MacBook use has no schedule. The Studio job scans runtime checkouts under
agent-runtime plus both autofix checkouts; manual commands default to Desktop/projects.
Desktop directory access stalled from launchd on this host, so scheduled jobs avoid that path. It never reads or deletes Codex sessions, application
databases, original media, Git worktrees/branches, or recovery archives. It sends no messages.

The default is a dry-run:

```sh
python3 agent-loops/cache-hygiene/maintenance.py
python3 agent-loops/cache-hygiene/maintenance.py --apply
scripts/install-launchd-tasks.sh --task cache-hygiene
```

The installer is Studio-only. It builds the native retired-cache helper and registers the
hourly job without RunAtLoad. Pause using `touch state/cache-hygiene/disabled`. The latest
plan and result are in that state directory. Free space below 150 GiB sets a warning in the
report; it does not cancel jobs or prevent user work.

Rules:

- Clear owned inactive Remotion bundles older than 24 hours. Compile caches have the same
  age threshold and are retained while Node/Vitest jobs are detected.
- Remove Git-ignored dependencies in idle projects and nested apps, plus worktree build caches after seven
  days. Preserve active processes, open handles, container bindings, tracked files and source.
- Retire the staging deterministic mobile-readiness cache when its directory index exceeds
  4 MiB or its last update is over seven days old. Require five minutes of quiet, no open
  cache handles, and no readiness process/container. Task reports and the paid LLM cache
  stay intact. Atomically create a fresh cache and remove the retired JSON/PNG files using
  a bounded native stream, rather than enumerating millions of file attributes into memory.
- Retain unrecognized cache entries for inspection. The native helper refuses fresh cache
  paths, other directories, traversal, and wrong ownership.
- Bound dependency discovery to 60 seconds in a separate worker. Skip generated audit data
  and Unity state; record an incomplete scan instead of letting a stalled filesystem
  prevent temp-cache and mobile-readiness maintenance.

This complements the existing worktree and Docker janitors. It does not kill dev servers,
drop schemas, remove merged worktrees, discard unfinished temp stages, or expire recovery
data. Those need separate lifecycle and recovery policies. Product-level cache sharding
would further reduce directory pressure; the hourly overflow rule limits the current flat
format without changing the product or deploying an application release.

Before new large work, use `python3 agent-loops/cache-hygiene/disk-preflight.py -- pnpm build`.
The default requires 100 GiB remaining plus a 20 GiB growth reservation. Studio CI's
job-start hook reserves 20 GiB per active worker; low space refuses the new job and
leaves existing jobs/services running. Register using the canonical installer:
`scripts/install-launchd-tasks.sh --task ci-disk-budget`, then restart only idle runners.

Manual and scheduled runs share one lock even when report directories differ.
The global disabled marker also applies to custom report directories.
