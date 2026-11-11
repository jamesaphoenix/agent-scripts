# Release and image retention (A Series / MinisForum)

`jud-image-retention.py` ports the Synology rules (`agent-loops/synology-retention`)
to the shared Docker daemon. `jud-image-retention.timer` runs it hourly. It is a
**dry run unless `--apply` is given**, and the shipped service is a dry run until you
add the drop-in below.

## Rules

- Per project under `/srv/jud/<project>/` it reads `current.json`, the optional
  `healthy-releases.json` (a list, or a dict keyed by project) and `releases/*`
  (`release.json`, `compose.json` or compose yaml image lines).
- Kept: the current release, the two most recent healthy ones, any release younger
  than 48 hours and any release directory bind-mounted into a container. Other
  release directories are pruned (at most 10 per run).
- Never removed: an image referenced by **any** container (running or stopped,
  matched by name, digest, short ID and, in live mode, exact ID from
  `docker inspect --format '{{.Name}} {{.Image}}'`), or by a kept release.
- Only images whose every reference is in the configured allow-list are candidates
  (exactly Synology's five repositories). The Octospark `renderer` and `media-runtime`
  repositories (about 300 GB here) are **not** included: they are pinned by the native
  compute artifacts (`current-render`, media), which this tool does not read. Add them to
  the config only after those pins are made a protected source. Untagged images with no references become
  candidates after 14 days. Images younger than 48 hours are kept.
- Never `--force`. Removal is by reference (`docker image rm repo:tag`), at most 10
  images per run (`--max-images 0` removes the cap for a deliberate reclaim).
- `--apply` takes an exclusive non-blocking flock on `/srv/jud/deploy.host.lock` and
  each existing project's `deploy.lock`; if any is busy the run does nothing.
- Fail closed. If a project directory exists but `current.json` cannot be read, or a
  project has no recorded healthy rollback yet (`bootstrap_hold`, as on Synology),
  nothing in that product's repositories is touched.

CI-built images (`octospark-renderer:ci-*`, `octospark-media-runtime:ci-*`,
`*-ci-postgres`) are deliberately **not** in the allow-list: they belong to the CI
daemon and are handled by `../cleanup.py` once item 3 (rootless CI daemon) is applied.

## Use

```sh
# on the host, as root: plan only
python3 /opt/jud-ci/agent-loops/a-series-ci/retention/jud-image-retention.py --config /etc/jud/image-retention.json
# deliberate reclaim report: largest unreferenced images, per-repository totals, build cache
python3 .../jud-image-retention.py --report --top 40
# from the MacBook, nothing executed on the host: read-only SSH snapshot, then plan locally
python3 collect-snapshot.py jamesaphoenix@jamesaphoenix-ai-series > snapshot.json
python3 jud-image-retention.py --from-snapshot snapshot.json
python3 jud-image-retention.py --from-snapshot snapshot.json --report
```

`collect-snapshot.py` runs only `docker image ls`, `docker ps -a`, `docker inspect
--format` (names and image IDs, no environment), `docker system df` and `cat`/`ls` of
the metadata files. Anything the SSH user cannot read is recorded as `unreadable`.
`--from-snapshot` never touches Docker or the filesystem and refuses `--apply`.

Sizes are per-image and overlap through shared layers; the real reclaim is smaller
than the sum. Measure with `docker system df` before and after.

## Apply

1. Copy `retention/` to `/opt/jud-ci/agent-loops/a-series-ci/retention/` (root, 755) and
   `image-retention.example.json` to `/etc/jud/image-retention.json` (root, 644; edit if
   needed).
2. `sudo install -m 644 jud-image-retention.service jud-image-retention.timer /etc/systemd/system/`
3. `sudo systemctl daemon-reload && sudo systemctl enable --now jud-image-retention.timer`
4. Let it run dry for a day: `journalctl -u jud-image-retention.service -n 80`. Check the
   SUMMARY line and that nothing in "would remove" is in use.
5. Enable deletion with a drop-in, not by editing the unit:
   `sudo systemctl edit jud-image-retention.service` and add
   ```
   [Service]
   ExecStart=
   ExecStart=/usr/bin/python3 /opt/jud-ci/agent-loops/a-series-ci/retention/jud-image-retention.py --apply --config /etc/jud/image-retention.json
   ```

## Rollback

- Stop deletion: `sudo systemctl revert jud-image-retention.service` (removes the drop-in),
  or `sudo touch /srv/jud/maintenance/retention-disabled` (the unit's `ConditionPathExists`
  skips it). Fully off: `sudo systemctl disable --now jud-image-retention.timer`.
- Deleted images are re-pulled from Artifact Registry by digest on the next deploy;
  deleted release directories are not recoverable, which is why healthy rollbacks and
  the 48 hour grace period are kept.

Tests: `python3 -m unittest discover -s tests -p 'test_a_series_image_retention.py'`.
