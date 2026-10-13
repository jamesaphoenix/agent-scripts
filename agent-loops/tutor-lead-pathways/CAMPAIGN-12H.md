# Twelve-hour tutor discovery

## Explicit extra window on 13 October

James requested another twelve hours after the current run. The original window
and its sealed job stay unchanged. A registered Studio activation job waits until
23:43:51 UTC on 13 October and until both original owners have finished. It then
starts `tutor-lead-pathways-20261013-extra-12h` and
`bucks-private-tutors-20261013-extra-12h`. The extra window ends at 11:43:51 UTC
on 14 October, or 12:43:51 BST. Activation checks run every minute until handover;
the new owner health checks and contact audit run every fifteen minutes.

The request JSON is pinned in the launchd plist by SHA256. Preparation verifies
the sealed paid receipts and preserves unsettled request keys. The new DataForSEO
ceiling is USD100 minus the sealed lifetime exposure. The existing USD3 Decisions
reserve is inherited once, with classification disabled because that allowance
is exhausted. No new classifier budget is created. SDK usage carries forward
the cumulative four-million-token admission ceiling; an unconfirmed prior SDK
turn holds that lane. The public crawler inherits its seen URLs, records and
receipts, with at most 12,000 additional attempts and 32,000 cumulative attempts.
The original per-site, branch and geography boundaries continue to apply.

Searches and SDK research prioritise 11+ and private academic tuition. The
original frozen baseline remains the comparison for total additional contacts.
Previously audited contact exports are hash-verified and deduplicated when
carried forward. Advertised contacts remain research records, with
`outreachEligible=false`. There is no outreach or expansion beyond Bucks and
Milton Keynes. A partial preparation is held for review; a failed installation
retries the already prepared stage without resetting its budget or deadline.
Terminal cleanup retains caches, exports, raw evidence, budget ledgers and source
awaiting PR review.

## Original window

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

The user's 13 October subject correction is recorded separately in the active
campaign's `discovery-scope.json`. Its four subjects are English, maths, verbal
reasoning and non-verbal reasoning, with explicit 11 plus and primary tuition
also useful. Music-only teachers and other-subject-only tutors are excluded from
the campaign contact export. A tutor offering maths and piano remains relevant.
Names, search keywords, county headings and neighbouring adverts never supply
the service claim. Previously audited 11 plus claims from the tutor's own advert
headline remain relevant even when absent from its separate subject tags.
Generic GCSE or SEND tags alone require subject review.

The optional policy keeps this infrastructure reusable for generic campaigns.
Missing policy preserves their existing scope; an invalid policy fails closed.
The SDK obtains its prompt from the same Python policy reader and records the
policy SHA256. Paid selection favours requested subjects, retains generic tutor
queries and holds explicit other-subject searches without marking them searched.
Prepared manifests, query payloads, deadlines and spending ledgers stay intact.
The extra window copies the validated policy from its sealed parent.

`additional-contacts.json` and its CSV contain the matching contacts. The broad
audit is retained as `additional-contacts-all-subjects.json`; excluded records
and reasons are in `contacts-outside-subject-scope.json`. The separate scope
summary reports these counts. Historical broad totals must not be presented as
subject-qualified leads. Existing frozen checkpoints and caches are retained.

Childcare's published Bucks subject routes for `11-Plus`, `Maths-Tutor`,
`English-Tutor` and `Primary-up-to-11-years`, including their bounded pagination,
are permitted seed pages. Other counties, childcare jobs, reviews and account
routes remain excluded. A filtered directory page does not qualify its tutors:
each person's own profile must still establish service and locality evidence.

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

Query selection and metrics share the same source-group function. Successful
fallback queries retain their parent tactic. Audited profiles reached through
recursive directory indexes supply a separate source-yield signal; they do not
become direct paid-request attribution or alter the contact count. Source yield
can therefore favour productive directories even when a search returned an
index rather than the person's final profile.

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

The separately prepared continuation admits up to 4000 URLs per directory,
retaining its cumulative 10000 attempts, eight pages per ordinary site and the
existing branch gates. Native access holds defer searches exclusively targeting
unavailable directories without marking them searched or reserving money.
Mixed-source queries and accessible directories continue.

If that stage reaches its 10000 cumulative attempts before the original deadline,
and the same USD3 lifetime Decisions cache still has budget, the monitor prepares
`tutor-focused-20261013-12h-remainder`. This separate registered stage permits
20000 cumulative attempts, including every inherited attempt. Preparation requires
a terminal parent and its released writer lock. The previous job is unchanged;
geography, directory quotas, ordinary-site limits and recursion gates stay fixed.
API failures and exhausted classification budgets do not start another stage.
Seed publication, profile collection, yield metrics and terminal cleanup include
the prepared remainder. No new classification allowance is reserved.

The campaign monitor also publishes hash-addressed classifier seed batches from
new verified provider receipts, completed SDK results and saved Go page text.
The classifier owner imports them itself every 30 seconds, retaining existing
lineage and immutable page, site and branch limits. The monitor never writes to
the live frontier or submits a paid decision. Invalid batches are held while
valid batches continue. Saved text avoids another website fetch and the shared
Decisions cache avoids buying the same classification again. A continuation
waits for new seeds between batches until its deadline or page limit.

SDK turns use at most six search batches, eight source inspections and four
targeted known-lead index reads. Results are returned when that bounded research
is complete, rather than filling the twelve-minute timeout. The existing token
ceiling, number of turns and authorisation window remain unchanged.

Public professional registers add another contact source. The BDA adapter
replays a cached, hashed body and reads each named card separately. It requires
an advertised county, teaching service and contact route; assessment-only
cards and possible duplicates stay held. The campaign monitor combines these
with the directory delta in `exports/additional-contacts/new.csv` and JSON,
checking previous business phone aliases as well as names and source URLs.
The combined export remains research and grants no outreach eligibility.

TuitionList adds public profile and index paths to the profile collector. Index
cards only seed a person's page; the audit requires their own heading, a named
lesson-location statement and the subjects section. Related searches and nearby
cards never supply those fields. Profile URLs drop enquiry fragments before
cache lookup. Existing cached bodies and receipts can be replayed without a new
request. Enquiry forms are not used for research or marketing, and emails and
phone numbers are never inferred from the form.

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
# Reviewing public contact adverts

The paid owner captures its loaded Git SHA at startup. Deployment checkpoint reports are
observations, never runtime inputs: changing or damaging them cannot stop discovery or
relabel a running owner's version. An unavailable version is reported as unknown.

Save reviewed own-site or public-advert contacts in
`review/public-contact-reviews.json`, outside the published lead-import folder.
`reviewed_contacts.py` requires a campaign Go
checkpoint, its SHA256 and result index, a contiguous source unit, literal name, town or county and service
quotes, an advertised email, phone or own-site contact page, and reviewer/time provenance. Contact
pages require a saved contact or enquiry link on the same website host; arbitrary links and other
businesses' forms are held. No form is submitted. Classified parent-frontier
evidence can also be reused by page key and evidence SHA256 after its Go fetch scratch has been
cleaned up. SQLite is opened read-only and
the original Go checkpoint hash remains in the contact evidence. A malformed or changed item
is held individually. Shared names or contact routes are held for identity review. Own-site host
aliases and frozen-baseline phone/name matches do not create additional contacts. The campaign
watcher combines accepted entries with directory and professional-register contacts, checking
current-run duplicates again. These are research claims; the review cannot grant outreach eligibility.

For adverts beyond the crawler's text limit, cache the public HTML body and successful response
receipt under `public-source-review/`. An `html_advert` review verifies both hashes and one complete
DOM unit selected by its unique ID. Names, locality, services and contact routes must all belong
to that unit. Another advert's email or an inferred village location is held. Use robots-permitted
public pages and preserve original receipts; this replay makes no network requests.
