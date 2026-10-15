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
consecutive unverified/failed responses, an exhausted plan, its budget or the
original deadline. Public-profile and qualification workers preserve their own
durable stop conditions. No emails, domain purchases, SDK turns or Anthropic API
key are part of this pass.

At terminal completion each monitor cleans only its worker's scratch. Retain
exports, receipts, page caches, ledgers, reservations and source awaiting PR review.
The MacBook is not responsible for the jobs' lifetime.
