# agent-scripts

Host automation, native compute services, CI support, and bounded agent research loops.

- `agent-loops/` - host maintenance, native compute, NAS retention, CI setup and reusable research loops
- `launchd-tasks/` - Mac task registry and central installer documentation
- `runner-hooks/` - platform-specific CI hooks and deployment SSH setup
- `pi-extensions/` - global pi (pi.dev) extensions: Brave `web_search`/`web_fetch` and subscription failover, symlinked into `~/.pi/agent/extensions/` by `dotfiles/install.sh` (see `pi-extensions/README.md`)

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
- Spotlight exclusions for the Studio's CI runner / VM disk / model trees (manual, needs sudo): `agent-loops/spotlight-exclusions/README.md`

## Hosts

| Host | Responsibility |
|---|---|
| MacBook Pro | Authoring checkout, laptop hygiene, worktree maintenance, manual project sync and pi extensions |
| Mac Studio | Native rendering/media, Qwen gateway and llama backend, ML services, cache and Docker hygiene, invoice harvest |
| Synology NAS | Octospark and Trace Learn application stacks, tunnels and NAS-local release retention |
| A Series | Organisation CI runners (`a-series-1` to `-4`), deployment SSH support and CI cleanup |

## Development And Deployment Model

The MacBook Pro is the authoring source. Studio is a runtime target for native compute and
host maintenance. Synology owns primary application hosting and its own maintenance; A Series
owns CI. See [Mac tasks](launchd-tasks/README.md),
[A Series CI](agent-loops/a-series-ci/README.md), and
[NAS retention](agent-loops/synology-retention/README.md).

Review changes from a current, clean checkout or isolated worktree, run the relevant dry-run
or status command, then deploy only the required paths. Studio's conventional paths are:

```text
/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/
/Users/jamesphoenix/Desktop/projects/just-understanding-data/agent-scripts/
```

The first is the launchd runtime; the second is a visibility/project-sync copy. Runtime fixes
must return to the MacBook source before a later sync. The convenience deployment commands are:

```bash
scripts/deploy-to-mac-studio.sh --dry-run
scripts/deploy-to-mac-studio.sh --live
```

The helper syncs the whole source tree to both Studio paths, excludes Git/state metadata,
and does not delete remote files. It does not itself retire removed scripts or prove that
launchd is using the new version. Inspect the diff and preserve runtime-only jobs before use.

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
in `gui/<uid>` run inside that session, so the workaround is: scp a 0600 file
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

Current responsibilities:

- `octospark-compute` runs Studio's native render/media workers, Qwen admission gateway and
  fleet watcher. `llama-server` supplies the local inference backend.
- `host-docker-cleanup`, `cache-hygiene` and `playwright-cli-reaper` maintain Studio resources.
  Docker remains in use for ML services.
- `freeagent-invoice-harvest` remains a Studio daily task at 06:30.
- `dev-docker-cleanup`, `worktree-janitor`, browser reaping and clone/fuzz pruning maintain the
  MacBook. Consult the registry for enabled tasks and schedules.
- `project-sync-macbook-to-mac-studio` is a manual, one-way, non-deleting project copy. Preview:

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

## Cross-repo drift check (trace-learn and octospark)

`scripts/cross-repo-drift.py` compares the files listed in `config/cross-repo-identical-files.txt`
between trace-learn and octospark at `origin/staging` (read with `git show`, working trees are not
touched) and exits 1 if any has drifted or disappeared. Entries suffixed `  # comments` ignore comment
wording. Run `scripts/cross-repo-drift.py --fetch` before porting work, or add it as a weekly launchd
task via `launchd-tasks/registry.json` if drift keeps slipping through. Remove a path from the list
when the repos should deliberately diverge.
