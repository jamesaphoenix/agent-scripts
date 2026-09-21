# Spotlight exclusions

Keeps Spotlight away from directories that churn constantly and are never
searched: the four GitHub Actions runner work trees, the Colima CI VM disk,
Docker Desktop's VM disk, and the llama.cpp model files.

## Why

On 2026-09-09 the Mac Studio sat at a load average of 25 with `mds` at 50%
CPU and `mds_stores` at 16%, continuously, while llama-server itself used
under 5% CPU. Spotlight was re-indexing every file a CI job touched:

```
indexed files whose content changed in the previous 15 minutes
17347 /Users/jamesphoenix/actions-runner-3/_work3
16605 /Users/jamesphoenix/actions-runner-2/_work2
16469 /Users/jamesphoenix/actions-runner/_work
13122 /Users/jamesphoenix/actions-runner-4/_work4
```

Every checkout, `pnpm install`, build and Playwright run rewrites tens of
thousands of files, and each one is handed to an importer. Docker Desktop's
`Docker.raw` makes it worse: Spotlight types the 926GB sparse disk image as
`com.panasonic.raw-image` (a camera RAW photo) and re-imports it on every
write.

None of these trees are ever searched from Spotlight, so the exclusion costs
nothing.

## How it is applied

`mdutil` can only disable indexing per volume, and the `.metadata_never_index`
marker is not honoured for subdirectories on current macOS, so the only
reliable per-directory mechanism is the Privacy list in
`/System/Volumes/Data/.Spotlight-V100/VolumeConfiguration.plist`. That file is
root-owned, so applying needs **interactive sudo**: neither Mac has
passwordless sudo and this is deliberately not a launchd job.

```sh
agent-loops/spotlight-exclusions/exclude.py --status    # no sudo: per-path index churn
agent-loops/spotlight-exclusions/exclude.py --dry-run   # sudo read only: what would change
agent-loops/spotlight-exclusions/exclude.py --live      # sudo: add missing paths, restart mds
```

From the MacBook, against the Studio runtime copy (the `-t` gives sudo a tty):

```sh
ssh -t jamesphoenix@jamess-mac-studio \
  'python3 ~/agent-runtime/just-understanding-data/agent-scripts/agent-loops/spotlight-exclusions/exclude.py --live'
```

The script is idempotent: it only appends paths that are missing from the
list, never removes anything, and only restarts `mds` when it changed the
list. Paths come from `config/spotlight-exclusions.json`, keyed by the same
machine keys as `launchd-tasks/registry.json`.

After a change, Spotlight drops the excluded items from its index in the
background. `--status` shows the per-path count of recently re-indexed files
falling to zero over the following minutes.

## Removing an exclusion

Delete it from the config and then remove it in System Settings > Spotlight >
Search Privacy on the machine. The script never deletes entries, so a path
that was excluded by hand stays excluded.
