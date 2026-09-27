# Playwright CLI reaper

Backstop for Playwright CLI daemon and Chrome cleanup failures on macOS.

Playwright CLI sessions are intentionally detached from their caller. If an agent exits badly, a
daemon or its headless Chrome process tree can remain alive and consume CPU and memory. Current
Playwright CLI releases include `close-all` and `kill-all`, but the reaper also recognizes the
Playwright-specific browser profile paths so it can remove Chrome processes left behind after the
daemon exits.

Install the command on the current Mac:

```bash
agent-loops/playwright-cli-reaper/install.sh
```

Safe default cleanup:

```bash
playwright-reap --dry-run
playwright-reap
```

Default mode only selects Playwright-owned browser processes with PPID 1. It does not terminate a
browser that still has a live Playwright CLI daemon parent.

When no Playwright session should remain, close everything and clean leftovers:

```bash
playwright-reap --force --dry-run
playwright-reap --force
```

Force mode first runs `playwright-cli kill-all`, then sends TERM and finally KILL to any remaining
CLI daemon or browser process carrying these Playwright-specific markers:

- `playwright-core/lib/entry/cliDaemon.js`
- `playwright/cli.js run-cli-server`
- `playwright_chromiumdev_profile-*`
- `Library/Caches/ms-playwright/daemon/`

The reaper also removes unreferenced temporary `playwright_chromiumdev_profile-*` directories older
than five minutes. It does not remove persistent session data under `Library/Caches/ms-playwright`.

Inspect without changing anything:

```bash
playwright-reap --status
```

Abandoned sessions whose daemon is still alive are not orphans, so default mode leaves them. Add
`--max-age-hours N` to also terminate CLI daemons older than N hours together with their whole
process tree (headless Chrome and its helpers):

```bash
playwright-reap --max-age-hours 3 --dry-run
```

This runs hourly on both Macs as the `com.jud.playwright-cli-reaper` LaunchAgent, registered in
`launchd-tasks/registry.json` as `playwright-cli-reaper` (MacBook Pro) and
`playwright-cli-reaper-studio` (Mac Studio). Install it on the current machine with:

```bash
scripts/install-launchd-tasks.sh --task playwright-cli-reaper        # MacBook Pro
scripts/install-launchd-tasks.sh --task playwright-cli-reaper-studio # Mac Studio
```

The age threshold defaults to 3 hours; set `PLAYWRIGHT_REAPER_MAX_AGE_HOURS` when running
`install-launchd.sh` to change it. Logs go to `~/Library/Logs/playwright-cli-reaper.log`.

Run the focused tests:

```bash
agent-loops/playwright-cli-reaper/test.sh
```
