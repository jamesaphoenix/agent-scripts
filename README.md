# agent-scripts

Agent-oriented experiments, research loops, and ad hoc automation.

- `agent-loops/` - reusable loops for autonomous agent research (ralph, reverse-ralph, auto-research)

## Engineering Loops

Host-level engineering loops live in this repository so there is one place to inspect what is
running on the machine. Project repositories should keep project code, tests, skills, fixtures, and
repo-local commands. Long-running host scripts, schedules, launchd plists, registries, and logs
belong here.

Runtime jobs should use 1Password through the shared service-account-token helper, not through an
interactive `op` session. Cache `OP_SERVICE_ACCOUNT_TOKEN` once in the target Mac user's login
keychain, then launchd jobs can call `op read` non-interactively. If the token is missing, the job
should fail fast rather than prompting or hanging in the background. For higher-risk jobs, cache
narrower downstream secrets instead.

- Registry: `agent-loops/registry.json`
- Launchd task registry: `launchd-tasks/registry.json`
- Launchd task installer: `scripts/install-launchd-tasks.sh`
- All loop status: `agent-loops/status.sh`
- Runtime state and logs: `state/` (gitignored)
- Generic job wrapper: `lib/run-job.sh`
- Mac Studio VM sizing (Docker Desktop + Colima memory, restart consequences): `docs/mac-studio-vm-sizing.md`
- Spotlight exclusions for the Studio's CI runner / VM disk / model trees (manual, needs sudo): `agent-loops/spotlight-exclusions/README.md`

## Development And Deployment Model

Treat the MacBook Pro copy of `agent-scripts` as the authoring source of truth. Write, test, and
review hourly, daily, and weekly scripts on the MacBook first. The Mac Studio copy is a deployment
and runtime target for unattended jobs, not a place to make durable edits.

Recommended flow:

1. Edit and test the loop on the MacBook Pro.
2. Run the loop's dry-run or status command locally where available.
3. Deploy the finished script to the Mac Studio runtime and visibility paths.
4. Install or reload launchd jobs on the Mac Studio with `scripts/install-launchd-tasks.sh`.
5. Inspect logs and status from the MacBook over SSH.

Deploy with:

```bash
scripts/deploy-to-mac-studio.sh --dry-run
scripts/deploy-to-mac-studio.sh --live
```

Use these Studio paths consistently:

```text
/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/
/Users/jamesphoenix/Desktop/projects/just-understanding-data/agent-scripts/
```

The runtime path is for launchd execution. The Desktop path is for visibility and normal project
sync. Do not rely on edits made on the Mac Studio Desktop copy, because the MacBook to Studio
projects rsync may overwrite files with the same path. If emergency edits are made on the Studio,
copy them back to the MacBook before the next project sync.

## Shared 1Password Service Account

Cache the 1Password service account token once per Mac user:

```bash
scripts/cache-op-service-account-token.sh
```

The token is stored in this macOS login keychain service:

```text
op_service_account_token_cli_automation
```

Launchd wrappers should source `lib/op-service-account-env.sh` and require the token before calling
`op read`:

```bash
source /Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/lib/op-service-account-env.sh
agent_scripts_export_op_service_account --required
```

This avoids per-plist secret caching. The tradeoff is that any job running as that macOS user and
able to read this keychain item can use the service account token, so keep the 1Password service
account scoped to the smallest practical vault and item set.

## Caching secrets on the Studio over SSH

`security add-generic-password` fails from an SSH session on the Studio with "User interaction
is not allowed": the login keychain is only unlocked inside the GUI login session. Launchd agents
in `gui/<uid>` run inside that session, so the workaround (used 2026-09-04) is: scp a 0600 file
of `service<TAB>value` lines to `~/.config/agent-scripts/`, bootstrap a temporary RunAtLoad
LaunchAgent that loops `security add-generic-password -U` over it and deletes the file, then
bootout and remove the temporary plist. No Screen Sharing needed.

## Launchd Task Installer

Use one installer, on either Mac, for hourly, daily, and weekly launchd tasks:

```bash
scripts/install-launchd-tasks.sh --list
scripts/install-launchd-tasks.sh --dry-run
scripts/install-launchd-tasks.sh
scripts/install-launchd-tasks.sh --status
```

The installer reads `launchd-tasks/registry.json`, where every task names the `machine` it runs on
(`mac-studio` or `macbook-pro`; hostnames and users live once in the registry's `machines` block).
The same script runs on either Mac and only acts on that machine's tasks. `--audit` reports
registered-but-missing and installed-but-unregistered agents; `--table` regenerates the per-machine
tables in `launchd-tasks/README.md`. Studio jobs keep services alive; MacBook jobs only clean up
after the laptop's own tools.

Current loops:

- `ensure-dockerised-apps-online` (Mac Studio, every 5 min): `docker start`s any stopped container
  of every app in `config/docker-apps.json` (Octospark, Trace Learn, tx-agent-kit) in dependency
  order after a reboot or Docker Desktop restart. Not a deploy. Opt out with
  `state/ensure-dockerised-apps-online/disabled[.<app-id>]`.

- `temporal-worker-tailscale-health` (Mac Studio, every 1 min): checks every Temporal-backed app
  in `config/docker-apps.json` can reach the Hetzner Temporal server over Tailscale from inside
  its container, repairs a stale Tailscale route, then restarts the configured workers. Repairs
  are rate-limited to one per 10 min.

- `dev-docker-cleanup` (MacBook Pro, weekly Monday 09:30): removes dangling images, build cache
  older than 7 days and anonymous unattached volumes older than 30 days. Never touches tagged
  images, containers or named volumes.

- `worktree-janitor` (MacBook Pro, daily 08:30): removes worktrees whose branch content is already
  in main/staging (archiving uncommitted work first), kills api/worker/web dev servers older than
  24h that run from a worktree, and emails a digest of worktrees idle for 30+ days. Never touches
  Postgres, Redis, Temporal, Docker or Playwright. Config: `config/worktree-janitor.json`.

- `config/docker-apps.json` is the single list of long-lived compose apps on the Studio. Add an
  app there (validate with `python3 lib/docker-apps.py validate`) and both loops pick it up on
  the next deploy; no script edits.

- `playwright-cli-reaper`: manual macOS backstop for orphaned Playwright CLI daemons and headless
  Chrome process trees. Install it with `agent-loops/playwright-cli-reaper/install.sh`, inspect with
  `playwright-reap --status`, use the safe orphan-only mode as `playwright-reap`, or use
  `playwright-reap --force` only when no Playwright session should remain.

- `project-sync-macbook-to-mac-studio`: manual one-way rsync sync from
  `/Users/jamesaphoenix/Desktop/projects/` to
  `jamesphoenix@Jamess-Mac-Studio.local:/Users/jamesphoenix/Desktop/projects/`.
  It never deletes Studio files, so files that exist only on the Studio are kept.

Run a dry run before enabling launchd:

```bash
agent-loops/project-sync/macbook-to-mac-studio.sh --dry-run
```

## Shared Mail OAuth

Reusable Gmail OAuth configuration for `jamesaphoenix@googlemail.com` lives in
`config/mail-oauth.json`. It stores only 1Password refs and keychain service names, never secret
values.

Use it from Python scripts:

```python
import sys
sys.path.insert(0, "/Users/jamesaphoenix/Desktop/projects/just-understanding-data/agent-scripts/lib")
import gmail_oauth

token = gmail_oauth.access_token()
```

Use it from launchd shell wrappers:

```bash
export AGENT_SCRIPTS_MAIL_OAUTH_DISABLE_OP_FALLBACK=1
source /Users/jamesaphoenix/Desktop/projects/just-understanding-data/agent-scripts/lib/mail-oauth-env.sh
agent_scripts_export_mail_oauth jamesaphoenix-googlemail
```

For unattended launchd jobs, first export the shared OP service account token with
`lib/op-service-account-env.sh`. Cache the Gmail OAuth profile itself only when you want narrower
runtime access than the shared service account token.

Cache the profile into the macOS login keychain after secret rotation:

```bash
scripts/cache-mail-oauth.sh --profile jamesaphoenix-googlemail
```

## Job Runner

Use `lib/run-job.sh` for new host-level scripts that need consistent logging, locking, and exit
code summaries:

```bash
lib/run-job.sh \
  --id example-job \
  --state-dir state/example-job \
  --cwd /path/to/repo \
  -- command arg1 arg2
```

The runner writes timestamped logs plus `last-run.json` under the state directory.

## JUD FreeAgent VAT preparation

The shared `jud-freeagent-vat` skill is maintained in the sibling dotfiles repo and
installed for Codex and Claude Code. It combines the existing invoice collector,
portal/browser evidence collection and the existing 1Password service account.

```bash
scripts/jud-vat.sh check
scripts/jud-vat.sh snapshot YYYY-MM-DD YYYY-MM-DD --output state/jud-vat/period-before.json
scripts/jud-vat.sh login jus001.freeagent.com --username jamesaphoenix@googlemail.com
```

These commands do not change FreeAgent records. Snapshots require JUD's live
company identity and exact return dates, are created with private permissions, and
never overwrite an earlier snapshot. Login discovery returns secret references,
not resolved passwords. Set `JUD_INVOICE_COLLECTOR` only when the collector is not
in the usual sibling checkout. Its existing Studio collection schedule remains
in the central launchd registry; the skill does not install a second schedule.

Run helper checks with `python3 -m unittest discover -s tests -v`.
