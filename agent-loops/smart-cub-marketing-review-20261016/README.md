# Smart Cub marketing review, 16 October 2026

One task-owned Studio queue observes all 130 finished marketing pairs using the existing
`review-media.py` and its single authoritative US$100 budget. It launches one reviewer process
at a time, leaving one process slot for the separately orchestrated teaching review.

Install only this registered job with `scripts/install-launchd-tasks.sh --task smart-cub-marketing-review-20261016`.
The installer copies only this task and its secret helper into the Studio agent-runtime tree,
so launchd does not need access to the macOS Desktop-protected authoring checkout. The job runs
once at load, with no KeepAlive or schedule. It waits for both exact frozen-source
render receipts, verifies encoded hashes, and keeps durable status under
`~/render-tmp/smart-cub-refresh-20261016/workflow/marketing-review-dispatch-status.json`.
The official reviewer reuses current exact advisory caches and refuses unresolved paid jobs.
The queue stops on any cap refusal, failed review, changed protected context, operator stop file
`workflow/marketing-review-dispatch.STOP`, or two hours without a new reviewable pair.
A halted queue does not automatically restart. Reconcile the underlying state before an explicit
operator resumption. Never erase an uncertain paid job or release its reservation to continue.

Runtime credentials are sourced from the existing service-account helper. No key enters args,
files or logs. Uploaded provider files are cleaned by the existing reviewer. All decode/API work
stays on Studio. Observations and process completion grant no final passes. Separate per-output
agent judgements and a fresh exact-version ledger inventory remain required.

Retain output clips, review receipts, budget ledger, judgement notes, logs and status. After the
phase ends, unload this label and remove its installed task-owned plist. Do not stop another job
or delete active render checkpoints. Remove this dated registry entry only after the phase ends.

Scheduling uses Standard process policy and normal I/O priority, with explicit nice 10 and the
authorised concurrency limits. Do not add Background or LowPriorityIO throttling to these
user-requested media jobs. Task-local live corrections use supported taskpolicy -B on verified
own process trees; do not restart a paid request or change global host settings.

The explicitly authorised revision uses workflow/marketing-revision-dispatch-config.json.
This pins an isolated app,149 passed narration contracts, the frozen graph fingerprint and
protected context/implementation hashes. The original canonical teaching graph is refused as
a revision target. Marketing review cache uses the isolated revision out/review-complete directory.
Historical marketing evidence is copied byte-identically, with an explicitly hashed path map.
Only historical evidence can resolve through that map; current media and render receipts keep
their actual source roots. Canonical CTA and teaching files retain their original provenance.
Revision review admits one aspect and at most one paid request per invocation, saving its
checkpoint before considering the next aspect. A marketing-revision-review-dispatch.STOP
file stops new admissions after the active request is durably saved. It never cancels a paid
request or grants final approval. Revision status/logs have a separate marketing-revision
prefix, preserving the old checkpoint. Installation is inactive until the operator's kick.

The authorised pilot reserves four whole-clip review slots: two teaching, one marketing
and one corrected teaching. All consumers must use the same pinned workflow/review_admission.py
before two teaching shards are started. Family slots and exact media intents use inherited
flock leases. A review child retains its lease if its dispatcher dies; never unlink lease
files to recover capacity. A stop file prevents the next child admission, without cancelling
an active paid request. Admission does not change the shared USD 100 ledger or reconcile
uncertain requests. The standalone guard also accepts a single review command after `--`,
with --run-root, --family, --id, --aspect and the exact --output-sha256.
