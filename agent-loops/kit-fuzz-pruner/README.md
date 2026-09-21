# kit-fuzz-pruner

Hourly launchd job on the MacBook Pro that stops kit-fuzz render output from filling the disk
while the continuous kit hardening agent runs for days.

## Why

The hardening agent in `just-understanding-data` renders about 20 films a pass and keeps the
frames of any run it may look at again. That is roughly 3GB a day of gitignored PNGs under
`front_end/reports/kit-fuzz/<timestamp>-*/`. On 2026-09-18 it held 8.3GB across 352 run dirs
with the data volume at 93%.

## What it does

Scans the main checkout and every worktree (`.claude/worktrees/*`, `.worktrees/*`). Only
timestamped run dirs are candidates, and age comes from the stamp in the dir name.

| Age | Action |
|---|---|
| under 24h | nothing |
| 24h to 72h | delete everything except `*.json` and `*.mp4` (seeds stay replayable, films stay watchable) |
| over 72h | delete the whole dir, which is what `inspect.mjs` does by default without `--keep` |

Below 40GB free the tiers drop to 6h and 24h. Below 20GB free after pruning it posts a macOS
notification (at most one every 6 hours), because by then something else is filling the disk.

## What it never touches

- Top-level reports (`<stamp>.md`, `<stamp>.json`, `coverage.json`, ledgers). They are tracked
  in git and are what the run acts on.
- Named dirs (`camera-point-before`, `3d`, `video`, ...). Those are deliberate proofs.
- Any run dir named in `tools/kit-fuzz/`, `reports/kit-hardening/` or `vision-calibration.md`.
  The scorecard, journal, checkpoints and check sources cite run dirs as evidence.
- Any run dir with a file written in the last 6 hours. The agent replays seeds inside older dirs.

## Use

```bash
bash agent-loops/kit-fuzz-pruner/install-launchd.sh        # install or reinstall
DRY_RUN=1 bash agent-loops/kit-fuzz-pruner/prune-kit-fuzz.sh   # print what would go
touch state/kit-fuzz-pruner/disabled                        # maintenance opt-out
tail ~/Library/Logs/kit-fuzz-pruner.log
```

Every threshold has a `KIT_FUZZ_PRUNER_*` environment override, listed at the top of the script.
A line is logged only when something was pruned or the disk is low.
