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

This is installed as a manual MacBook command. It is deliberately not registered as a MacBook
LaunchAgent because this repository's unattended launchd tasks are installed on the Mac Studio
only. The upgraded Playwright CLI owns normal lifecycle cleanup; this script is the independent
manual backstop.

Run the focused tests:

```bash
agent-loops/playwright-cli-reaper/test.sh
```
