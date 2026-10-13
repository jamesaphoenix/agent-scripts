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
Owned Cloudflared logs above 256 MiB also produce metadata-only warnings in
`last-report.json` and `storage-warnings.jsonl`. These records include size, modification
time and whether an open handle was observed. Logs are never read, truncated, moved or
deleted by this job. Configure controlled service log rotation separately. Discovery
refuses symlinked/unowned log directories and stops after 128 directory entries.

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

Identical tracked worktree assets can also share APFS storage while remaining
independently editable. This is a manual operation, separate from the hourly cache job:

```sh
python3 agent-loops/cache-hygiene/cow-deduplicate.py \
  --worktree-root "$HOME/Desktop/projects/just-understanding-data/trace-learn/.worktrees" \
  --state-dir "$HOME/agent-runtime/disk-cleanup/cow-sharing"
# Review report.json, then repeat with --apply.
```

The default minimum file size is 256 KiB. Only supported asset/source file types tracked
by Git are considered. Database files, symlinks, hardlinks, flagged files, dependency
stores and active worktrees are excluded. Every replacement requires identical SHA-256
content before and after cloning and unchanged target metadata. Target permissions,
ACLs, timestamps and extended attributes are copied onto the clone. No hardlinks are
created, so later edits remain independent. The state records successful replacements
for repeat runs; actual free-space gains must be measured with df because du counts
shared extents in every file. An invalid Git registration is retained for review.

## Render frame retention

The hourly maintenance job also expires seven-day-old numbered PNGs in two known
output layouts: Claude Code episode `renders/overlay-*` sequences (`frame-N.png`)
and video-lab `runs/**/geometry/{wide,tall}` diagnostics (`audit-N.png`). It requires
five minutes of directory quiet, current ownership, no symlinks, no tracked source,
no open output handles, no rendering process, and no overlapping container bind.
Overlay cleanup also requires a final movie in the same episode that passes ffprobe.
Geometry cleanup requires its audit metadata. Native `final-frames` are preserved
because encode verification reads those PNGs, even when the final movie exists.
Original PNG assets, JSON audit results, Blender sources and all MP4s are retained.
Each cleared folder receives a `frames-pruned.json` receipt. Discovery is limited
to the known rendering projects inside configured project roots, including their
registered worktrees. Per-run deletion is bounded to two minutes; later hourly
runs continue the remaining eligible directories.

Manual review, dry-run by default:

```sh
python3 agent-loops/cache-hygiene/render_frames.py \
  --root "$HOME/Desktop/projects/just-understanding-data/just-understanding-data/artifacts/claude-code" \
  --state-dir "$HOME/agent-runtime/disk-cleanup/render-frames"
# Add --apply after reviewing last-report.json. Defaults: seven days and five minutes quiet.
```

For an explicitly authorized one-time cleanup, `--age-days 0` removes old completed
frame outputs without the seven-day retention period. Activity, media validation,
file identity and source protections still apply.
