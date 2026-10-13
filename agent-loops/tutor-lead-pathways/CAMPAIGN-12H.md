# Twelve-hour tutor discovery

The 13 October request is anchored at 11:43:51 UTC and ends at 23:43:51 UTC
(00:43:51 BST on 14 October). Preparing, recovering or restarting a job never
extends it. The target is 2000 to 3000 additional contacts for independent academic
tutors in Buckinghamshire and Milton Keynes, with 11+ prioritised.

`prepare_campaign.py` seals the authorisation, query plan, crawler digest and
baseline inventory. Previous business rows, audited people and unaudited pages
are already known. Reclassifying one does not count as discovering a new person.
CSV/JSON exports remain research; no outreach eligibility is assigned.

## Spending

The existing USD100 campaign cap is shared by all stages. Preserve USD66.35 of
earlier conservative DataForSEO exposure, including USD43 of uncertain holds.
Reserve the entire USD3 Decisions lifetime allowance even though its current
spend is lower. The new paid ledger has prior exposure 6935 cents and at most
3065 cents available. Historical uncertainty is imported as blocked request
identities. Its dollars are already in the prior floor and are never released or
charged twice. No uncertain query is submitted again.

Successful receipts are copied and hash verified. Public page bodies are reused
through hard links with separate receipt files. Native SDK research uses the
Studio's existing ChatGPT OAuth login and receives neither API keys nor provider
credentials. There is no Anthropic API key or fallback provider.

## Discovery and feedback

The paid runner interleaves Maps neighbourhood queries, Business Listings,
subject-specific public profiles, independent practice sites, professional
registers, specialist teachers and local adverts/newsletters. SDK research adds
named leads and source suggestions. Suggestions require tuition wording and an
approved town before becoming a paid query. The SQL keyword index stores
searched queries, observations, lineage and pending recursive expansions.

The runner explores source families before ranking their measured yield per
charged cent. New result URLs are a provisional reward, explicitly separate
from audited new identities attributed to a source. Identity audits run every
15 minutes. A duplicate advert, locality ambiguity, historical profile or name
collision does not inflate the accepted new-person count.

Campaign audits replay page evidence for profiles outside the frozen baseline.
The preparation step already verified and replayed the inherited cached pages.
Previously collected profiles remain in the preserved baseline and full profile
exports, but are excluded from the new-person audit instead of reparsing them
every 15 minutes. `excludedKnownProfileRows` records that distinction.

The Go crawler retains a depth of two and at most 15 new sites per branch.
Public-profile collection has at most 12000 additional attempts and runs until
the deadline, rather than stopping when a raw profile count reaches a target.
Sites that deny access and robots restrictions remain respected.

`campaign_watch.py` bridges results from the earlier classifier into the profile
collector. Once the earlier classifier is terminal, it prepares a separate
continuation using the same SHA256 Decisions cache, unchanged geography and
recursion gates, and the original campaign deadline. That continuation is
installed through the registered Studio launchd task. Paid search, free crawling
and audits have independent failure handling and checkpoints.

## Runtime and handoff

Author scripts on the MacBook. Push the scoped branch, then create a clean pinned
Studio checkout at `.worktrees/tutor-campaign-20261013-12h`. Runtime artifacts stay
in the existing repository's `state/`. Install only the five prepared campaign
tasks through `scripts/install-launchd-tasks.sh --task <id>`; the classifier owner
and watcher are registered but installed later by the campaign monitor.

The `tutor-lead-pathways-20261013-12h` directory contains `status.json`,
`campaign-status.json`, `metrics/latest.json`, `baseline/`, `paid.sqlite`, hashed
receipts, SDK events/results and final exports. The matching
`bucks-private-tutors-20261013-12h` directory contains cached HTML, profile CSVs,
identity audits and the contact delta. Check actual process commands, advancing
checkpoints and receipts, not just launchd registration or an idle process.

Terminal cleanup removes task-owned transient seed files and loopback SSH host
files after the owners finish. Keep exports, evidence, reusable caches, budget
ledgers and source awaiting PR review. After a verified merge, remove the job's
source checkout and merged branch. Preserve unrelated runtime changes.

## Provider references

- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [Maps Live API](https://docs.dataforseo.com/v3/serp-maps-live-advanced/)
- [Business Listings Search](https://docs.dataforseo.com/v3/business_data-business_listings-search-live/)
- [Business Listings pricing](https://dataforseo.com/pricing/business-data/business-listings-api)

Published Business Listings pricing is USD0.012 per task plus USD0.00036 per
returned item, up to 1000 items per response. The runner reserves USD1 before
each request and records verified provider receipts; it does not assume that
all requested rows will be returned.
