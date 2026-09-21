# worktree-janitor

Daily launchd job (08:30 local) on the **MacBook Pro** that keeps git worktrees and the dev servers running
from them under control. Config: `config/worktree-janitor.json`. Written 2026-09-04 when
worktrees had reached 325 GB (trace-learn alone 280 GB across 83 worktrees) and were growing
by about 18 GB a day.

## Rules, in order

1. **Merged worktrees are removed automatically.** "Merged" means the branch's content is
   already in any target branch (`main`, `staging`), decided by *any* of:
   - git ancestry (`merge-base --is-ancestor`), which sees true merge commits;
   - a no-op `git merge-tree --write-tree`, which sees squash and rebase merges by content.

   An old merged PR with the same branch name is not sufficient: a reused branch may
   contain new, unmerged work.

   Before removal: uncommitted changes are archived to `state/worktree-janitor/archive/` as a
   patch plus a tarball of untracked files; every dev server running from the worktree is
   killed; the repo's own `scripts/worktree/manage.sh remove <name> --yes` runs where it exists
   (it also drops the worktree's Postgres schema), otherwise `git worktree remove --force`; the
   local branch is deleted. Locked and detached worktrees are never removed.

2. **Dev servers older than 24 hours are killed.** API, worker, web (vite / wrangler / workerd /
   next) and their helpers (esbuild, tsx watch), node family only, and only when their cwd is
   inside a worktree. `pnpm dev` brings them back. Never touched: Temporal, Postgres, Redis,
   Docker, Colima, Playwright daemons, editors and language servers (see `neverKillPatterns`).

3. **Idle, unmerged worktrees go in an email digest.** No commit and no change to an
   uncommitted file for 30 days. Sent through Resend to the configured address, at most once a
   day and only when the list changes, with a weekly reminder otherwise. Each row carries the
   exact remove command. The janitor never removes these itself.

## Adding a repo

Append to `repos` in the config: `id`, `path`, `targets`, and optionally `removeCommand` (used
only for worktrees directly under `<repo>/.worktrees/`). Worktrees are discovered with
`git worktree list`, so location does not matter.

## Commands

```sh
agent-loops/worktree-janitor/janitor.py --dry-run     # full report, changes nothing, sends nothing
agent-loops/worktree-janitor/janitor.py --status      # last report
agent-loops/worktree-janitor/janitor.py --no-email    # rules 1 and 2 only
agent-loops/worktree-janitor/janitor.py               # the real thing
```

State: `state/worktree-janitor/` (`last-report.json`, `janitor.log`, `digest-state.json`,
`archive/`). Requires `gh` authenticated and the Resend key cached in the login keychain under
`agent_resend_api_key`.

## Install (MacBook Pro only)

```sh
scripts/install-launchd-tasks.sh --task worktree-janitor
```

Installs `com.jud.worktree-janitor`, daily at 08:30 local, no RunAtLoad; a missed slot fires on the next wake.
