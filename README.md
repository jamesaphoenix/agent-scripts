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

## Launchd Task Installer

Use one Mac Studio installer for hourly, daily, and weekly launchd tasks:

```bash
scripts/install-launchd-tasks.sh --list
scripts/install-launchd-tasks.sh --dry-run
scripts/install-launchd-tasks.sh
scripts/install-launchd-tasks.sh --status
```

The installer reads `launchd-tasks/registry.json`, filters tasks for the current Mac hostname and
username, ensures shared prerequisites such as the OP service account token when needed, then
installs or reloads the matching plists. Register only Mac Studio launchd jobs here. The MacBook
Pro is the authoring and control machine, not a launchd host for these agent loops.

Current loops:

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
