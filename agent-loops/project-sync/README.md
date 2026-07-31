# Project Sync Loop

Syncs the MacBook Pro projects directory to the visible Mac Studio Desktop projects directory with
rsync.

Source:

```text
/Users/jamesaphoenix/Desktop/projects/
```

Target:

```text
jamesphoenix@Jamess-Mac-Studio.local:/Users/jamesphoenix/Desktop/projects/
```

The loop is one-way from MacBook Pro to Mac Studio. It never passes `--delete`, so remote files are
not removed when the MacBook source changes or is incomplete. Files that exist only on the Studio
are kept.

This is not bidirectional sync. For `agent-scripts`, use the MacBook Pro as the source of truth:
write and test scripts there, then deploy them to the Mac Studio runtime path. Avoid durable edits
on the Studio copy unless you immediately copy them back to the MacBook before the next sync.

## Commands

Dry run:

```bash
agent-loops/project-sync/macbook-to-mac-studio.sh --dry-run
```

Live sync:

```bash
agent-loops/project-sync/macbook-to-mac-studio.sh --live
```

Status:

```bash
agent-loops/project-sync/macbook-to-mac-studio.sh --status
```

All loop status:

```bash
agent-loops/status.sh
```

## Launchd

Do not run this as a MacBook launchd plist. Launchd tasks are standardized on
the Mac Studio only. This script is manual for now: run a dry run, review the
output, then run live sync intentionally.

Runtime logs and JSON summaries are written under `state/project-sync/`, which is gitignored.
