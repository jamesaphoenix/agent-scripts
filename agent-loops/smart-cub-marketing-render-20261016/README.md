# Smart Cub marketing render handoff

One-shot Studio renderer resumes the frozen130-pair batch with the existing exact receipts.
It uses Node22, nice10, two renders with four workers each. No paid APIs or media approval.
Installation registers an inactive job: RunAtLoad and KeepAlive are false, with no schedule.
Before a manual kick, verify the existing controller command/cwd, send only that controller
SIGINT, wait for its cancellation cleanup and machine-wide lock release. Never remove a lock
blindly. Runtime code lives outside Desktop TCC in the task's agent-runtime directory.

Status: `~/render-tmp/smart-cub-refresh-20261016/workflow/marketing-render-dispatch-status.json`.
Logs: run-root `evidence/marketing-render-dispatch-{stdout,stderr}.log`.
The original batch report/receipts remain under the founder app's `out/` folder. Any changed
source fingerprint or renderer error stops without automatic restart. Completed exact receipts
skip; an interrupted unfinished output can render again. Retain exports/evidence/ledgers and
remove only the task's installed launchd plist after completion and verified cleanup.

Scheduling uses Standard process policy and normal I/O priority, with explicit nice 10 and the
authorised concurrency limits. Do not add Background or LowPriorityIO throttling to these
user-requested media jobs. Task-local live corrections use supported taskpolicy -B on verified
own process trees; do not restart a paid request or change global host settings.

The new revision handoff uses the explicit marketing-revision-dispatch-config.json, refuses
the canonical teaching app, and verifies149 passed narration contracts and protected input
hashes. It renders all compositions in registry order:38 CTA outputs first, then260 marketing
outputs. The existing source snapshot guard, global fallback, receipts and host lease remain
authoritative. Its revision status has a separate filename, preserving the stopped first batch.
Do not kick before the isolated teaching-correction renderer releases the shared host lease.
