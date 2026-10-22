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
- Mac Studio VM sizing (Docker Desktop + Colima memory, restart consequences): `docs/mac-studio-vm-sizing.md`
- Spotlight exclusions for the Studio's CI runner / VM disk / model trees (manual, needs sudo): `agent-loops/spotlight-exclusions/README.md`

## Host responsibilities and retirement audit

Read-only live inspection on **22 October 2026, approximately 14:48-14:50 UTC**, compared
GitHub `main` at `b981a5a` with Mac Studio, Synology and A Series. These are retirement
findings from the initial inspection. The retirement applied afterwards is recorded below.

| Host | Observed responsibility | Keep |
|---|---|---|
| MacBook Pro | Authoring checkout and laptop maintenance | Local hygiene, worktree maintenance, manual project sync and pi extensions |
| Synology NAS | Octospark and Trace Learn prod/staging API and general workers, Redis, ML inference, four application tunnels, Solar ROI and OTEL | Application stacks, NAS tunnels and NAS-local release retention |
| Mac Studio | Native rendering/media, Qwen gateway/backend, ML training/pollers/shared MLflow, auto-fix services | Native compute fleet, llama backend, cache and Docker hygiene, invoice harvest and browser reaper |
| A Series | Four organisation CI runners (`a-series-1` through `-4`) | Linux runners, deployment SSH support and CI cleanup timer |

The organisation runner API returned all four A Series runners online, one busy, and no
Studio runners. Studio's default Docker engine contained eight containers: seven ML
containers and `octospark-staging-native-cloudsql`. The NAS contained the four primary
application environments and four corresponding tunnel containers. Container inventory is
not an end-to-end feature test or a reboot/failover test.

### Retirement decisions

| Item | Recommendation | Evidence / condition |
|---|---|---|
| Studio `colima-ci`, `ci-vm-cleanup`, `ci-disk-budget` | Retire the old Studio installation; keep any reusable cleanup logic until callers are checked | Already disabled in main; Colima/CI plists archived with `disabled-20261020`; no Studio runners in the organisation fleet and no Colima Docker context in Studio's context list |
| Studio `cloudflared-tunnels` | Finish retirement in the registry; archive the four old Studio plists | All four launchd labels are disabled and absent from the loaded list; NAS runs their replacement tunnels. Main still incorrectly enables this task. Keep NAS tunnels |
| `temporal-worker-tailscale-health` | Retire this Docker-specific schedule, or replace it with explicit native-worker checks | Still runs every minute, but its live status has an empty `apps` array. All four application entries are disabled; the sole enabled dev entry has Temporal disabled. Its green result does not prove native worker polling |
| `ensure-dockerised-apps-online` | Retire if the shared dev stack is no longer required; otherwise narrow and repair it | Only enabled entry is `trace-learn-dev`; no matching dev containers appeared in the default-engine inventory. Loaded job last exit was 75 and last successful wrapper summary was 20 October. Confirm the intended engine and lock state before removing it |
| Historical tutor discovery/watch/recovery tasks | Keep retired schedules out of the active registry; archive campaign-specific code after caller checks | Main has removed the dated tutor entries; Studio's loaded list contains none. Old MacBook checkout still had them enabled, so do not deploy that checkout |
| Completed Smart Cub one-off agents | Archive completed 16-17 October plists and task-owned runtime scripts after checking delivery/checkpoints | Studio has 31 `com.jud.smart*.plist` files, including a newer 21 October renderer. The loaded 16-17 October jobs shown were idle with exit 0; idle alone is not proof of complete delivery. Preserve exports, receipts and unmerged work; review the newer renderer separately |
| `runner-hooks/job-completed.sh`, `job-completed-dryrun.sh`, `install-disk-preflight.sh` | Candidates for a legacy Mac-runner archive | Mac-specific cleanup and installer; Studio runners are gone. Check references in workflows, installer code and retained runner configurations before deleting source |
| `runner-hooks/job-started.sh`, `job-completed-linux.sh`, `setup-deploy-ssh.sh`, `lib/ci-admission.py` | Keep pending A Series deployment reconciliation | Main provides Linux CI/admission support. Live `/opt/jud-ci/runner-hooks/` only listed deployment SSH files, so installation of the documented admission/completion hooks was not established |
| `llama-server` | Keep | Live backend listens on `127.0.0.1:8082`; the Qwen admission service listens on Studio's Tailscale address at port 8080. These are complementary services |
| Studio `host-docker-cleanup` and `cache-hygiene` | Keep | Docker still hosts active ML services; cleanup has a successful 22 October summary. Retain native-image protection and avoid reviving old primary-host assumptions |
| Studio `com.jud.synology-retention` | Keep retired; do not reinstall | Main disables it and no live Studio label was found. NAS-local retention is the owner |

### Drift to fix before installing or deploying

- **Use current main.** The original MacBook checkout was at `d8911ea` (16 October), with
  unrelated dirty files. Studio runtime's Git metadata reported `b878cf7` (30 March), although
  newer runtime files were present. Deployment uses rsync excluding `.git`, so that SHA is
  not a reliable manifest of the running scripts. Compare file hashes or record a deployment
  manifest; a blanket sync from an old checkout could restore retired jobs.
- **Reconcile the launchd registry.** Main still enables the old Studio tunnel group. Live
  Trace Learn auto-fix runners/proxies and Smart Cub one-off jobs are absent from main's
  central registry. Both Trace Learn auto-fix runner labels had last exit 1, while their proxy
  processes were running. Investigate those failures before classifying the services as obsolete.
- **Reconcile A Series source and runtime.** Main documents `/opt/jud-ci/agent-loops/a-series-ci/`
  and `jud-actions-runner-N.service`; live units are
  `actions.runner.Just-Understanding-Data-Ltd.a-series-N.service`. The live cleanup unit invokes
  `/opt/jud-ci/cleanup/cleanup.sh`, scheduled at 04:15 plus up to 30 minutes of jitter, rather
  than the documented 04:10 Python job. Its script skips active CI, prunes stopped containers
  and dangling images older than seven days, and caps build cache at 20 GiB. This is different
  from the checked-in Python cleanup policy. Runner `.env` hook settings were not readable in
  this audit, so do not claim two-job admission is installed merely from source documentation.
- **Keep NAS maintenance local.** `/volume1/docker/jud/maintenance/retention-last.json` showed
  apply mode with no errors, nine removed releases and ten removed images on 22 October.
  DSM scheduler inspection as `mydocker` did not expose the application task, so this audit
  verifies the recent report, not independent scheduler ownership or reboot persistence.
  See [NAS retention](agent-loops/synology-retention/README.md) for the intended DSM setup.

Retire in this order: remove obsolete enabled registry entries, archive confirmed-complete
one-off installations, reconcile A Series deployment, then archive source with no remaining
callers. Preserve healthy rollback releases, budget ledgers, receipts, final exports and
active/unmerged work. Do not remove Docker volumes based on this inventory.

### Retirement applied on 22 October 2026

At 14:55 UTC the approved Studio retirement disabled/unloaded obsolete labels and moved
**38 plist files** out of `~/Library/LaunchAgents/`: 30 idle, successful 16-17 October Smart
Cub one-offs, four disabled application tunnels, the Docker recovery and Temporal health
jobs, and two previously disabled Colima/CI janitor plists. The newer 21 October renderer
was preserved. One-off task source and checkpoints remain available; a zero exit is not
proof that every requested deliverable was completed.

Studio rollback archive and exact source/destination manifest:

```text
/Users/jamesphoenix/.local/state/agent-scripts-retired/2026-10-22/
  manifest.json
  LaunchAgents/
  runtime/
  desktop/
  sync-result.json
```

Six legacy tasks are disabled in the source registry and their installer entries are
non-installing retirement records. The same changes were merged by task ID into Studio's
runtime and Desktop registries, preserving unrelated entries. All historical Compose
recovery targets are disabled in both copies and source. The three Mac-only runner hook
files were removed from active `runner-hooks/`: source is retained under
`archive/2026-10-22/mac-runner-hooks/`, and deployed copies are in the Studio rollback
archive. Shared Linux hooks, CI admission code and reusable janitor/recovery source remain.

Verification found every archived label absent from the loaded launchd list. Native
render/media/Qwen/fleet-watch/llama processes retained their PIDs; Docker ML services
remained running. All four A Series runner units and its cleanup timer remained active.
NAS tunnels and primary containers were inspected without mutation; its Octospark staging
container set changed between snapshots, so the post-change inventory does not establish
that environment's health. Four installer unit tests passed. No volumes, application data,
exports, budget ledgers or receipts were deleted, and no NAS/A Series configuration changed.

The registry drift and A Series source/runtime discrepancies above remain follow-up work.
The source retirement is recorded alongside the host audit in this repository.

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

Current responsibilities:

- `octospark-compute` runs Studio's native render/media workers, Qwen admission gateway and
  fleet watcher. `llama-server` supplies the local inference backend.
- `host-docker-cleanup`, `cache-hygiene` and `playwright-cli-reaper` maintain Studio resources.
  Docker remains in use for ML services despite retirement of the primary application stacks.
- `freeagent-invoice-harvest` remains a Studio daily task at 06:30.
- `dev-docker-cleanup`, `worktree-janitor`, browser reaping and clone/fuzz pruning maintain the
  MacBook. Consult the registry for enabled tasks and schedules.
- `config/docker-apps.json` describes Studio Compose recovery targets, not NAS applications or
  native workers. All historical recovery entries and the two old recovery schedules are disabled.
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
task via `launchd-tasks/registry.json` if drift keeps slipping through. The list came from a blob
comparison on 2026-09-29; the reasoning per file is in `~/Desktop/cross-repo-test-duplication.md`.
Remove a path from the list when the repos should deliberately diverge.
