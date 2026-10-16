# Corrected40 Studio launch preparation

Proposal only. No task registry entry, approved configuration, install or kick is created by this preparation.

`launch.py` is an argument-only wrapper. Before root reviews the actual immutable blend plan, `approved-batch.json` intentionally does not exist. It will later contain exactly contract `smart-cub-corrected40-launch/v1`, pinned `script` and `plan` file references (absolute path, bytes, SHA256), and an absolute task-owned `status` path. No invented plan hash or default source selection.

The command is the pinned Studio Python, `-B <script> run --plan <path> --plan-sha256 <hash> --status <path> --execute`. The reviewed `blend-corrected-batch.py` retains all domain gates: exact20/40 scope, registered sources and CTA approvals, audited helper/activation pins, truthful final source predecessor, both host leases, 50 GiB floor, serial hardware encoding and complete verification. The host wrapper does not weaken or reproduce those gates. Root will review any late cloze V2 predecessor update in that script before approving the plan. Derived20 proof remains bound by the late V2 source predecessor.

`run.sh` normalises the canonical Studio TMPDIR and executes nice10. The official installer first runs metadata-only launcher verification, so absent/unreviewed binding fails before plist or runtime changes. Its plist is Standard with normal IO, RunAtLoad=false and KeepAlive=false. The separate registry-proposal.json is not activated.

Three pure tests exercise missing approval/no exec, exact argv and byte tampering, non-Studio host and external status rejection. No media or provider operation occurs in these tests. No source registration, budget mutation, paid call or final-quality approval is authorised by this wrapper.
