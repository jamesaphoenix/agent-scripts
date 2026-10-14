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
- Only four exact Artifact Registry API/worker repositories are eligible. Unknown
  dangling images, Solar ROI, Redis, cloudflared, collectors, volumes, build caches
  and other application data are excluded.
- Acquire all four existing deployment locks and a janitor lock before observing
  or deleting. If a deployment is active, skip the run. Unhealthy services,
  malformed metadata, unsafe paths or symlinks fail closed.
- Remove at most ten old release folders and ten images per run. Never force
  image deletion. Docker retains layers shared with retained images.

## Installation and operation

Author in the MacBook agent-scripts repository, push the reviewed branch, and
deploy through the Studio checkout. Register and install only on Studio:

```sh
scripts/install-launchd-tasks.sh --task synology-release-retention
```

The LaunchAgent runs hourly and at load. The MacBook never owns the job lifetime.
It executes the versioned Python source through SSH on the NAS, without secrets
in arguments or output. No third-party credentials are needed.

By default the NAS only observes and records healthy release receipts. After
reviewing a successful dry run and live host health, enable deletion by creating
the empty private file `/volume1/docker/jud/maintenance/cleanup-enabled` on the
NAS. Remove that marker to return to observe-only mode. Touch
`~/.local/state/synology-retention/disabled` on Studio to skip the job entirely.

NAS evidence: `/volume1/docker/jud/maintenance/retention-last.json` and
`healthy-releases.json`. Studio evidence:
`~/.local/state/synology-retention/last.json`, `job.log`, `job.err.log`.
No secret file contents are included in reports. Historical failed releases are
never treated as verified rollback points merely because a folder exists.

Manual observation from the authorised caller:

```sh
ssh mydocker@100.123.72.113 'python3 -' < agent-loops/synology-retention/retention.py
```

Tests: `python3 -m unittest discover -s tests -p test_synology_retention.py`.
