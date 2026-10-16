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
