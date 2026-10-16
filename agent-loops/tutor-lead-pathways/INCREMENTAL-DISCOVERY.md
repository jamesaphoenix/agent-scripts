# Additional discovery window

The 15 October request authorises another USD50 for twelve hours, anchored at
21:17:40 UTC and ending 16 October at 09:17:40 UTC. This is a new window. No old
deadline, ledger setting, uncertain charge or SDK admission guard is reset.

`incremental_discovery.py` owns a separate USD50 bank. It imports historical
request identities and receipts for deduplication, while their charges and holds
remain in the original USD100 bank. The original bank retains its conservative
USD99.01 exposure. Maximum combined discovery exposure is USD149.01; the old
USD0.99 headroom is unavailable to this owner. No reconciliation releases old
reservations. Classification reuses the separately approved USD10 Decisions
cache, including its existing charges, held submissions and configuration.

`prepare_incremental.py` prepares three native workers: paid query seeding,
public-profile crawling and owned-site Go crawling with Decisions qualification.
The registry installs each worker and its fifteen-minute monitor on Mac Studio.
Saved searches use semantic identities, receipts and pages use SHA256, and paid
reservations precede submissions. Uncertain requests cannot be replayed.

Discovery covers the existing 44 Bucks and Milton Keynes towns. Maps queries use
plain subject phrases; organic queries also explore owner sites, local adverts,
directories, public profiles and professional registers. Source novelty guides
query ordering, with every tenth choice exploring less-used tactics. Search
results remain nominations until website/profile evidence supports identity,
subjects and locality. Directory workers nominate no more than fifteen profile
URLs from one query. Qualification retains its existing branch, hop and site caps.

The handoff, manifests, budgets, source SHA, observations, source yields and
monitor reports live in the three runtime state directories. The paid worker
reserves USD0.02 per Maps request and USD0.05 per organic request. These are
maximum reservations, not reported provider prices. It stops after five
consecutive uncertain submissions, an account/billing error, an exhausted plan,
its budget or the original deadline. Empty results are neutral. Repeated engine
or query errors pause their tactic, leaving other sources available.
Public-profile and qualification workers preserve their own
durable stop conditions. No emails, domain purchases, SDK turns or Anthropic API
key are part of this pass.

At terminal completion each monitor cleans only its worker's scratch. Retain
exports, receipts, page caches, ledgers, reservations and source awaiting PR review.
The MacBook is not responsible for the jobs' lifetime.

## Ten-hour recovery

The 16 October request authorises a new ten-hour window from 08:50:02 UTC to
18:50:02 UTC, with thirty-minute native checks. It adds no money. The recovery
manifest references the original USD50 `paid.sqlite` directly; its spent amount,
reservations, SHA256 receipts and failed identities remain unchanged. No new
paid ledger is created. The original twelve-hour manifest and deadline remain
as historical evidence.

The earlier owner stopped after five settled responses mixed internal search
engine errors with empty results. Those outcomes do not establish an account
outage. Regression coverage runs past five engine errors and two empty results
to a successful request, while account failures and uncertain charges still
stop safely. Provider code meanings are documented in the
[DataForSEO error reference](https://docs.dataforseo.com/v3/appendix/errors/).

Two of every three query slots are reserved for Maps and plain organic searches,
so novelty from operator queries cannot monopolise discovery. The monitor keeps
reporting genuine terminal account/budget stops until the saved deadline; it does
not repeatedly submit paid work. It can reconcile the legacy outage label only
from settled, hash-verified failure receipts and never releases uncertain holds.

`prepare_recovery.py` seals a stopped, drained profile worker for replacement,
preserving its old job, deadline, records and fetch receipts. It refuses live or
nonempty parents. The new profile and qualification stages retain reusable
context and their shared Decisions bank, with all three native monitors set to
1,800 seconds. Final cleanup retains paid evidence and source awaiting PR review.

## Reduced remaining budget

James's 16 October instruction limits all further provider spending to USD15
combined across DataForSEO and Decisions. `spend_limit.py` attaches the same
SHA256-sealed instruction to both existing SQLite banks. Original bank settings,
decision contracts, receipts and uncertain reservations remain unchanged.

Each provider acquires one shared file lock before reserving a request in its
own ledger. Admission counts both providers' charges and unsettled reservations
above the saved spending baseline. Existing holds also reduce the available
amount. Original provider caps still apply. Missing or changed policy evidence,
disagreeing bank attachments and insufficient headroom prevent new submissions.
Restarting workers cannot recreate the allowance. Cached request identities stay
readable after the spending ceiling is reached.

`endpoint_probe.py` compares Google Local Finder, Bing organic and Domains by
HTML Terms using up to fifty cents of that shared allowance. It owns the same
paid-bank lock while the regular paid owner is drained, then resumes that owner
within its original deadline. Request SHA256 identities and failed/uncertain
receipts cannot be replayed. Two consecutive trials without new nominations
stop that endpoint. Each request nominates at most fifteen public websites;
owned evidence and qualification remain required. Source observations report
returned URLs, duplicates, held reasons, cost and qualification intake hashes.

## Actual agent reviews

The native health probes are separate from James's request for the assistant to
inspect the run every thirty minutes. `agent_review_dispatch.py` queues prompts
into the existing Codex conversation at minutes 00 and 30. It does not start a
new agent, use an SDK turn or change the discovery budget. The Studio owns the
schedule and uses pinned SSH to invoke the MacBook's existing Codex queue.

Each slot keeps a source snapshot and a delivery receipt. An accepted queue
message is not a completed review. Only the model writes `reviews/<slot>.json`
with its actual inspection time, findings and actions. An uncertain delivery is
held rather than resubmitted. The last prompt requests a final result and cleanup
audit after the original deadline; delivery retires within a bounded grace period.

The current conversation is hosted on the MacBook. That Mac and its app-server
must remain available for these reviews to execute. The Studio crawler continues
independently when the MacBook sleeps. Missing or delayed agent reviews remain
visible in the saved receipts and snapshots, without claiming they happened.
