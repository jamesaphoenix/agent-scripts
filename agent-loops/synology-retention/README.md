# Synology application release retention

The NAS pulls immutable images. Reusing a tag does not remove old layers, and
overwriting a release loses the ability to identify or roll back deployed code.
This job observes the four healthy Octospark/Trace Learn environments hourly and
removes old, unused application images and private release bundles safely.

- Keep the current release and two distinct previously observed healthy releases
  per environment, including their image digests and private configuration.
- Keep every release folder and image younger than 48 hours. Until a project has
  at least one recorded healthy rollback, retain all its historical releases and
  all images in its shared application repositories. Bootstrap is deliberately
  conservative; new successful releases establish the rollback history.
- Protect images referenced by **any** container, including stopped containers.
  Also retain release folders used by any container bind mount.
- Only the five exact Artifact Registry API/worker/ML repositories are eligible
  for referenced-image cleanup. Images with no tag or digest become eligible
  after 14 days when no container uses them, covering replaced Solar ROI images.
  Referenced Solar ROI, Redis, cloudflared, collectors, volumes, build caches
  and other application data are excluded.
- Acquire all four existing deployment locks and a janitor lock before observing
  or deleting. If a deployment is active, skip the run. Unhealthy services,
  malformed metadata, unsafe paths or symlinks fail closed.
- Remove at most ten old release folders and ten images per run. Never force
  image deletion. Docker retains layers shared with retained images.

## Installation and operation

The job runs entirely on Synology, as `mydocker`, through DSM Task Scheduler.
The NAS keeps the script, retention engine, healthy-release ledger and schedule.
Neither Mac, SSH nor 1Password is needed at runtime. The shell entrypoint uses
Synology's existing Python 3 for the tested retention logic and GNU timeout for
a 420-second process limit with a 15-second kill grace.

Deploy this directory to `/volume1/docker/jud/maintenance/synology-retention/`
owned by `mydocker`, with private directory/file permissions. Then on the NAS:

```sh
/bin/sh /volume1/docker/jud/maintenance/synology-retention/cleanup.sh --observe
sudo /usr/bin/python3 /volume1/docker/jud/maintenance/synology-retention/install-dsm.py
```

The installer creates or updates the uniquely named **JUD application release
retention** user-defined script task. It runs every day, every hour at minute 23,
from 00:23 through 23:23 in NAS local time. DSM owns the persistent schedule,
including after reboot. The installer verifies the saved task and refuses to
replace an unrelated task with a conflicting name, owner or command. It does
not send email notifications. Do not hand-edit `/etc/crontab`, which DSM manages.

The scheduled command is:

```sh
/bin/sh /volume1/docker/jud/maintenance/synology-retention/cleanup.sh --apply
```

Deletion still requires the empty private marker
`/volume1/docker/jud/maintenance/cleanup-enabled`. Removing it returns the task
to observe-only mode. Create `maintenance/cleanup-disabled` to skip the task
entirely, or disable it in DSM Control Panel > Task Scheduler. `--observe`
always avoids deletion, even when the enable marker exists.

NAS evidence: `/volume1/docker/jud/maintenance/retention-last.json` and
`healthy-releases.json`. The report is replaced, not appended. DSM records task
completion and exit status. A timeout has a nonzero exit status and may leave
the previous JSON report, so check its timestamp as well as scheduler status.
No secret file contents are included in reports. Historical failed releases are
never treated as verified rollback points merely because a folder exists.

The former `com.jud.synology-retention` Studio LaunchAgent is retired. Its
registry entry is disabled and its SSH runner/installer are removed. During
migration, verify a real DSM-triggered run before unloading and removing that
specific Studio plist. Retain old run evidence, but not an active second scheduler.

Tests: `python3 -m unittest discover -s tests -p 'test_synology_retention*.py'`.
