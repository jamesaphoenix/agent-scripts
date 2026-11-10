# Host Docker janitor

Reclaims stale release images and BuildKit cache on a shared Docker daemon.
Host-level, cross-product: CI runners, production, and every sibling product
(octospark, trace-learn) share one `dockerd` on the Mac Studio (and, via its
own cron install, on the Hetzner standby box). This is why the script lives
here instead of in any one product repo - a copy embedded in a product repo
drifts the moment a new sibling product starts tagging images on the host.

## What changed from the original octospark-only version

The original allowlist-based protection (`docker ps -a --filter
label=com.docker.compose.project=octospark-staging`) had silently protected
nothing for a long time: the live stacks run as `octospark-staging-live` /
`octospark-prod-live`, not the names the allowlist matched. Protection now
comes from `protect_referenced_container_images`, which walks every container
on the host (running or stopped, any project) and protects whatever image it
references - it cannot drift out of sync with any product's naming, because it
never names anything.

## Install

**Mac Studio (launchd):**
```bash
bash scripts/install-launchd-tasks.sh --task host-docker-cleanup
```
Runs daily at 03:30 local. See `install-launchd.sh` for the artifact-dir
defaults (every CI runner slot x product combination it can find).

Build cache defaults to a 24-hour age filter and a 10 GB storage budget, with
the installed buildx CLI's supported budget flag. The host context is explicitly
selected for this process (`DOCKER_CLEANUP_CONTEXT=default`), and its matching
builder is used unless `DOCKER_CLEANUP_BUILDER_NAME` overrides it. This leaves
the user's saved Docker context unchanged and prevents selected-context drift
from directing maintenance to CI or breaking Buildx pruning. Recent or in-use
cache can exceed the budget temporarily.
Release tags also require 24 hours since both image creation and local tagging
(`DOCKER_CLEANUP_IMAGE_MIN_AGE_HOURS`). A months-old upstream image can have been
pulled moments ago, so its build date alone does not prove it is stale. Missing
or invalid local tagging metadata retains the image. Age is refreshed immediately
before removal to protect tags reused after discovery. The age helper requires
Python 3; it reads inspection metadata through stdin and emits no image contents.
Container inventory or inspection failures abort cleanup before any pruning.
Docker's nanosecond timestamp precision is normalized for older system Python
interpreters before parsing, while preserving timezone-aware age comparisons.
Stopped containers and all volumes are preserved by default; stopped-container
pruning requires `DOCKER_CLEANUP_PRUNE_STOPPED_CONTAINERS=1`.

**Linux host sharing a daemon with prod (e.g. the Hetzner standby):** there is
no launchd there, so this loop is deployed as a plain cron entry instead - see
`install-cron.sh`.

Digest-pinned pulls are included even when Docker lists their tag as `<none>`.
They use the same container, release-artifact and local-age protections as tags.
This matters on the standby, where deployment pulls `repository@sha256:...`.
The 24-hour age guard keeps recently pulled rollback images. Set
`DOCKER_CLEANUP_ARTIFACT_DIRS` to the product checkout artifact directories for
longer rollback retention. The cron log shows candidate references and the
amount reclaimed; a successful run that continually finds no candidates while
storage grows needs investigation.

## Manual use

```bash
DRY_RUN=1 agent-loops/docker-cleanup/cleanup-local-docker.sh   # preview only
agent-loops/docker-cleanup/cleanup-local-docker.sh              # actually clean
```

## Maintenance opt-out

Registry-driven installs respect the standard opt-out convention: touch
`state/docker-cleanup/disabled` to skip a run without uninstalling.

## Native renderer and media-runtime images

The Studio installer protects both current native compute release proofs under
`~/.local/state/octospark-compute/release-staging.json` and `release-prod.json`.
Native workers may be idle without any container referencing their image, so
container inventory alone is insufficient. `DOCKER_CLEANUP_NATIVE_PIN_FILES`
accepts colon-separated proof files; every file must contain valid digest-pinned
`rendererImage` and `mediaImage` values. Missing or malformed proofs abort cleanup
before mutation. With these protections configured, old `tx-agent-kit/renderer`
and `tx-agent-kit/media-runtime` images join the eligible repositories.

`DOCKER_CLEANUP_NATIVE_ENV_FILES` (default `env/render.env` and `env/render-staging.env`
under the same state directory) additionally pins `RENDER_HOST_RENDERER_IMAGE` from each
file that exists, because during the renderer split the running `render` role may use an
image no receipt names. Missing files are skipped; a present file without a valid digest
aborts cleanup.

The current image digests and their local image IDs are retained, including tag
aliases. The latest 20 release artifacts in each configured runner directory also
protect rollback images, including keys such as `RENDERER_IMAGE_AMD64`. Images
still require 24 hours since both creation and local tagging. Native cleanup is
disabled when no proof files are configured; the separate Colima job continues
to target its local CI renderer/runtime repositories.

## Hetzner image-only policy

Hetzner uses `cleanup-hetzner-images.py`, installed in
`/opt/host-docker-cleanup`, rather than the general host janitor. The managed root
cron runs at 03:30 through `install-hetzner-cron.py`. The installer first proves a
valid dry-run, backs up the existing crontab, and replaces only the managed line.
The general Studio cleaner and its native pin support remain unchanged.

This policy only removes explicitly enumerated, immutable application image IDs
from the Octospark API, worker, ML, renderer and media repositories and the Smart
Cub API and worker repositories. Both image build time and local tagging time
must be at least 48 hours old. Removal never uses `--force`. There is no global
prune, dangling-image cleanup, cache cleanup, or deletion of containers, networks,
volumes, releases, source trees, logs, backups or rollback metadata.

Every running or stopped container protects its image, including restore jobs.
The current native canary proofs and current native symlinks protect renderer and
media images even when no render container is active. Shared image aliases are
protected by image identity. Unknown repositories and missing age metadata are
retained. A native deployment lock is held throughout each run; container and
release protection is inventoried again before every removal. Compose standby
scripts currently expose no shared host deployment lock. Concurrent image use is
also guarded by Docker's non-forced removal, and any changed protection inventory
aborts the remainder of the plan.

The required `state/rollback-ledger.json` has `version: 1` and `projects` entries
for `octospark-staging`, `octospark-prod`, `trace-learn-staging` and
`trace-learn-prod`. Each entry is a chronological array of verified successful
release receipts. A receipt contains `artifact`, `artifactSha256`, `sourceSha`,
`images` and an `evidence` URL identifying the successful release run. Use the
module's `read_artifact(path, product)` to derive the first four fields from an
existing immutable `images-*.env` file, then add the verified evidence URL.
Secrets or runtime env files must never enter this ledger.

Keep current plus **two distinct image bundles per product and environment**.
Repeated source commits deploying identical images do not fill rollback slots.
Verify the successful standby leg and release gate, including an in-job background
standby success when its separate retry job is skipped. Retain the corresponding
artifact files unchanged. Missing protected digests must be re-pulled by immutable
digest before cleanup can resume. The script archives each accepted ledger under
`state/receipt-archive/`, and never deletes these receipts or artifacts.

After a new image bundle is deployed, cleanup **refuses to run until the audited
success history is refreshed**. Daily observations cannot establish history when
multiple deployments occur between runs. Review every intervening successful
release, update each affected environment's chronological records, verify current
native pins agree with the newest receipt, and atomically replace the ledger while
holding `state/hetzner-images.lock`. Do not infer release success or environment
from artifact modification time. The final successful deployment gate calls `record-hetzner-release.py` to capture
every deployment automatically, including multiple releases between cron runs.
A missed or failed hook deliberately leaves cleanup blocked until the history is
reviewed. The collector validates current service images and native canary proof,
always holds the cleanup/ledger lock, and additionally holds the native deployment
lock for Octospark receipts and native-history bootstrap. Smart Cub application-only
receipts do not wait on unrelated native deployments. It verifies every referenced image exists, archives the prior
ledger, and appends the receipt without removing historical records. It is
idempotent for identical retries. Intermediate build or standby steps must not
call it before the other required release legs have succeeded.

```sh
python3 /opt/host-docker-cleanup/record-hetzner-release.py \
  --project trace-learn-staging \
  --artifact /opt/trace-learn/deploy/artifacts/images-<source>-amd64.env \
  --evidence https://github.com/Just-Understanding-Data-Ltd/smart-cub-learning/actions/runs/<run-id>
```

Only after a verified cutover may an operator add a project to the optional
`retiredProjects` array in the ledger, under the cleanup lock. A retired project
retains its historical current plus two rollback bundles and every stopped
container image, but no longer requires running application services. Native
compute remains independently active after a staging application moves to
MinisForum. Known project names are required, duplicates are rejected, and
unexpected missing services in an active project still stop cleanup. Application
receipt ingestion for a retired project is rejected until an operator explicitly
removes that state; native-only ingestion remains valid.
No project is retired by the installer or automatic observations.


### Native history after application cutover

The same ledger must also contain `nativeProjects`, with chronological `staging`
and `prod` arrays. Each receipt identifies independently verified `media` and
`render` immutable artifacts, their checksums, compute source SHAs and native
image pins, the owning successful release URL and verification provenance.
The renderer may deliberately remain on an older release while staging media
changes. Native bundle identity includes compute sources as well as image pins:
a changed executable with reused Docker images is still a distinct rollback.
Cleanup retains the current plus two distinct native bundles per environment
and the actual renderer image from the render artifact plus the actual media
image from the media artifact. An unused selected renderer image is not a live
native pin and does not need pulling solely for retention. It always
protects the actual `current-render` artifact separately, since production may
advance the shared renderer after staging's completed canary.

Bootstrap this history explicitly from audited successful native deployments,
using `audited_native_record(environment, artifact_path, evidence_url)` from the
cleanup module to derive records from retained immutable artifacts. Confirm the
native canary and final gate succeeded for every source and environment. Supply
a JSON object containing both chronological environment arrays, then run:

```sh
python3 /opt/host-docker-cleanup/record-hetzner-release.py \
  --bootstrap-native-history /protected/audited-native-history.json
```

Bootstrap refuses an existing native history, incomplete rollback coverage,
changed artifacts, a mismatch with current completed native proofs or missing
local images. It archives the old ledger and preserves application history and
retirement state. This is never an automatic daily inference from release folders.

The final successful Octospark gate continues the ordinary recorder for a
Hetzner application release; it records native history in the same transaction.
When staging applications run on MinisForum, the same final gate records only
native compute, without pretending that the retired Hetzner app was deployed:

```sh
python3 /opt/host-docker-cleanup/record-hetzner-release.py \
  --project octospark-staging --native-only \
  --evidence https://github.com/Just-Understanding-Data-Ltd/octospark/actions/runs/<run-id>
```

Native-only mode accepts no artifact override. It reads the exact completed
`release-<environment>.json`, its media artifact and explicit
`renderRelease`/`renderSourceSha` (legacy receipts use the media release for both).
It validates source and image pins, the current media symlink and environment
hash, and local images. The cleaner separately validates actual shared-render
configuration and symlinks. Both use the cleanup and native deployment locks.
Unknown native history remains a hard stop, including several deployments
between daily runs. No release directory, runtime env, receipt, container,
volume or network is deleted by this policy.

```sh
# Read-only image plan (the checked receipt archive is maintained).
python3 /opt/host-docker-cleanup/cleanup-hetzner-images.py \
  --plan-out /opt/host-docker-cleanup/state/reviewed-plan.json

# Apply exactly that candidate list, rechecking protection before each image.
python3 /opt/host-docker-cleanup/cleanup-hetzner-images.py \
  --apply-plan /opt/host-docker-cleanup/state/reviewed-plan.json

# Install only after the ledger and local rollback images have been verified.
python3 /opt/host-docker-cleanup/install-hetzner-cron.py

# Pause both old and new host cleaners; remove this file to resume.
touch /opt/host-docker-cleanup/state/disabled
```

Tests: `python3 -m unittest discover -s tests -p 'test_hetzner_*.py'`
and `python3 -m unittest discover -s tests -p test_host_docker_context.py`.
