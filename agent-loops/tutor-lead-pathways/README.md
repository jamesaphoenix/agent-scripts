# Tutor lead discovery through five pathways

## Automated qualification and contact enrichment

`qualification_seeds.publish` feeds observed public websites into the registered
qualification owner. Its content-addressed batches live in `website-seeds/`.
The owner imports each batch once into SQLite, rejects private routes and foreign
context, and preserves existing owners' decisions and failed fetch holds. Up to
100 websites per batch and 2,000 new owners per pass are admitted. Names and search
sources are metadata, never subject or locality proof. Go fetching, Decisions
requests and final exports use the existing deadline, per-site limits and shared
classification allowance. Status records imported candidates and rejected batches.

Completed qualification batches now publish structured research profiles under
`exports/automated-qualification/`. The publisher reconstructs each settled
Decisions request and usage receipt, replays its owned page context, checks
identity and locality, and holds uncertain duplicate matches. Its `accepted.json`
contains model-qualified research profiles, separate from the existing
source-reviewed contact export. `held.json` records why a candidate needs further
extraction or review. The publisher never calls an API or changes the paid ledger.

Missing public email does not block a profile. Those accepted records enter
`email-enrichment-queue.json`; emails observed in owned page text retain their
source URL and remain unverified. No enrichment provider or Codex SDK agent is
launched by this stage. The existing crawler can collect observed contact/about
links within its site allowance; later enrichment can consume this queue.

Decisions evaluates predicates, fixed choices and scores. Free-form extraction
requires [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
through the Responses API. The current stage uses existing crawler/profile fields
and literal evidence, without introducing another model or spending allowance.

## Tutors' Association public directory

`tta_scraper.py` adds a source-specific scraper to the registered profile worker.
Capture every rendered page of the public Find a Tutor directory, retaining its
reported total, page offsets and observed member IDs in an index. The publisher
rejects gaps, duplicate IDs and foreign routes. It writes one SHA256-bound seed
batch, prioritising the observed Buckinghamshire subset. Repeating publication
reuses that batch without resetting the worker's counters or deadline.

```bash
python3 agent-loops/tutor-lead-pathways/tta_scraper.py \
  --index state/tutor-lead-pathways-20261013-12h/public-source-review/tta/public-index.json \
  --profile-run-dir state/bucks-private-tutors-20261013-12h
```

The directory's internal bulk endpoint is disallowed by robots.txt and is not
used. Index capture uses the published page controls. Public profile GETs obey
both `Crawl-delay` and `Request-rate`; the current TTA rules require at least
sixty seconds between requests. Successful and failed observations reuse the
existing SHA256 cache. Native launchd owns the worker and its fifteen-minute
checks. The separately authorised continuation inherits unvisited URLs, cached
pages and attempt counts.

Profile scheduling rotates among hosts ready to fetch, keeping individual
profiles ahead of town and subject indexes. A long cooldown on one directory
does not delay another ready host. When all hosts need to wait, the earliest
candidate is selected. Cached receipts need no host wait or repeat request.
The downloader still enforces each host's persisted cooldown and robots rules.

An observed local search subset can supply `priorityUrls` in its SHA256-bound
seed batch. At most a hundred unique admitted URLs from that same batch move
ahead of the pending queue. Seen URLs stay seen and repeated imports do nothing.
This changes review order only; a search filter never qualifies a tutor.

Qualification reads only the profile's labelled personal and business fields.
County claims supply locality, while subject specialisms supply services. Age
levels, qualifications, navigation and neighbouring members cannot establish a
subject. Only advertised contact fields supply email, phone or website. Franchise
and platform affiliations remain excluded. The national index is a source cache;
the campaign export still requires Buckinghamshire or Milton Keynes locality,
the configured subjects, evidence replay and conservative identity deduplication.

The campaign watcher replays cached profiles through `publish_websites` on each
health check. It verifies each receipt and page hash, then feeds own advertised
websites into the existing Go crawler. Conflicting towns, franchises and known
other-subject-only profiles are held. A missing subject stays a crawl candidate
and cannot become a contact without further evidence. Page failures are isolated
per member. Website seeds retain the original profile URL and body hash, and
reuse the existing page allowance, site lineage and fifteen-site branch limit.
Separate member URLs retain separate identities in the general discovery code.
No directory total or contact route grants permission to send outreach.
Wild Apricot's repeated empty field placeholders are ignored, while duplicate
named fields fail closed. A versioned replay recovers previously downloaded
profiles from verified cache receipts without new requests or counter resets.
Conflicting city/county claims stay outside the accepted export when the named
town is evidenced in another authority. Initial checks cover Leighton Buzzard,
Linslade and Wicken, with council references alongside the rules. Existing cached
records receive the same check at export time.
When a member puts England, UK, United Kingdom or Great Britain in the County
field, its own City field must establish locality. A conflicting county still
holds the profile; a national value cannot qualify an unknown or outside city.

Own locality headers can omit a comma before Buckinghamshire or qualify a known
town with Milton Keynes. The locality check accepts those formats and `St.`
abbreviations while retaining the original source text. A later nearby town in a
list cannot establish the primary locality. Bare ambiguous places remain held.

Audited tutor yield used for search selection follows the campaign's subject
policy. Music-only and other-subject-only profiles do not earn an audited-contact
reward. Supported directory profiles retain distinct URL identities, so a result
for one member cannot take credit for another member on the same directory.
An own organisation name matching a business in the frozen baseline holds a
person for identity review, even without a matching website or phone. Separate
people remain separate; the organisation match does not merge their identities.

## Public PDF adverts

`pdf_adverts.py` inspects an already cached public newsletter. It makes no network
or provider requests. Supply its SHA256 receipt, a one-based PDF page and the
complete advert rectangle in points at 72 dpi. Poppler (`pdfinfo`, `pdftotext` and
`pdftoppm`) is required on the authoring and runtime machines.

```bash
python3 agent-loops/tutor-lead-pathways/pdf_adverts.py \
  --run-dir state/my-campaign \
  --pdf state/my-campaign/public-source-review/newsletter.pdf \
  --receipt state/my-campaign/public-source-review/newsletter.receipt.json \
  --page 28 --bounds 20 313 380 267 \
  --output state/my-campaign/public-source-review/tutor-advert
```

The command saves a PNG and a review template. Inspect the complete advert,
then add its own name, locality quote, subject claims and advertised contact
route to `review/public-contact-reviews.json`, together with the reviewer and
review time. Keep the issue date and any uncertainty in `notes`. A newsletter's
circulation area does not establish the tutor's own locality.

The campaign watcher replays the receipt, PDF bytes, page rectangle and image
hash before exporting a contact. It rejects edited text, foreign cache paths
and contact routes absent from that rectangle. One bad document is held without
stopping other reviews. Existing identities and the configured subject scope
still apply. Dated adverts remain research leads with unverified availability;
no email permission is inferred. There is no automated admission from PDF text.

## Keyword expansion and upstream classification

`candidate_review.py` applies saved Decisions API responses to a frozen business
inventory. It opens the decision cache and crawl frontiers read-only, reconstructs
the exact model request and verifies its SHA256, response schema and charged usage.
It never calls a provider or resets a budget. Public profiles do not share decisions
with other profiles on the same directory. Saved Go checkpoints can supply context
for the remaining decision queue; search queries and URL towns cannot supply missing
subject or locality evidence.

Observed redirects can recover cached context when the original and final URLs
belong to the same website identity. Replay keeps the final URL in the exact
decision request. Other directory profiles and hosted tenants cannot inherit that
context. A failed fetch can use such a verified saved observation on resume;
uncertain paid submissions remain held. Business directory titles and normalised
trading-name aliases are possible duplicate matches, rather than new contacts or
automatic merges.

```bash
python3 agent-loops/tutor-lead-pathways/candidate_review.py \
  --candidates state/my-campaign/exports/businesses.json \
  --frontier state/my-focused-crawl/frontier.sqlite \
  --decisions-cache state/tutor-decisions-cache.sqlite \
  --crawl-root state/my-campaign \
  --contacts state/my-campaign/exports/additional-contacts.json \
  --scope-run-dir state/my-campaign \
  --output-dir state/my-campaign/review/candidate-qualification
```

The output retains the original inventory, one assessment per research row, a
provisional tutor shortlist and deduplicated requests missing from the cache.
Institutions, irrelevant pages, directories, missing context and held responses
remain separate. A positive model result is a research candidate. It still needs
owned service, locality, identity and contact-route evidence before entering the
audited tutor export. No result grants outreach eligibility. Pending requests are
proposals only and require available authorised classifier allowance before use.

The [Decisions API guide](https://developers.openai.com/api/docs/guides/decisions)
describes the shared text input and named classification questions used by the
existing classifier. This replay preserves that original request contract.

`qualify_inventory.py` runs the separately authorised 14 October classification
batch through the Studio registry tasks `tutor-qualification-20261014` and its
fifteen-minute watcher. The new USD10 ledger leaves the original USD3 classifier
cache and USD100 discovery limit unchanged. Its immutable configuration records
James's additional budget instruction, candidate inventory hash, subject policy,
crawler binary and original campaign deadline. The maximum combined allowance is
USD110, including the original classifier reserve and unsettled requests.

The batch reuses recorded owned-page text, then fetches missing public contexts
with the existing Go fetcher, four workers, no recursive expansion, a minute
between requests to the same site and eight attempted pages per site. Source
access holds and uncertain old decisions remain held. New candidate and fetch
states survive in SQLite. The model evaluates the candidate's own requested
subjects, independent status and local coverage. Music-only offers, national
navigation lists and neighbouring adverts cannot establish a qualifying offer.
Positive results remain research candidates until owned identities, contact
routes and duplicate matching are checked. `tutor-research-candidates.json` is
separate from the audited contact export.

`prepare_qualification_followon.py` prepares a newly authorised context window
from a completed classification stage. It freezes the current research inventory,
backs up candidate and fetch states, retains held Decisions submissions and
references the existing USD10 SQLite ledger through a configuration hash. It
does not create another paid allowance. The original stage and deadline remain
unchanged. The new deadline comes from the explicit window authorisation and
does not move when installation or recovery takes time.

```bash
python3 agent-loops/tutor-lead-pathways/prepare_qualification_followon.py \
  --previous state/tutor-qualification-20261014 \
  --run-dir state/tutor-qualification-20261014-context-12h \
  --candidates state/tutor-lead-pathways-20261013-extra-12h/exports/businesses.json \
  --authorization state/bucks-private-tutors-20261014-context-12h/authorization.json \
  --profile-run-dir state/bucks-private-tutors-20261014-context-12h
```

The registered `tutor-qualification-20261014-context-12h` owner and its watcher
run on Studio. Own websites can supply up to three context pages, selected from
the original URLs and observed links within the same owner. About, teacher,
contact, locality and tuition pages receive priority. Shared website-builder
tenants keep separate identities. Existing fetch claims, site counts and
cooldowns survive stage changes and restarts.

Directory context is delegated to the public-profile worker, preserving that
worker's host limits. Its saved receipts must pass body-hash and owned-profile
ID checks before classification uses public fields. Newly discovered local
profiles are imported only after those checks; known audited source URLs are
skipped. The watcher checks every fifteen minutes and can refresh a completed
classification batch while its profile producer is still active. The immutable
deadline and paid-request holds take precedence. The shared ledger also has an
exclusive owner lock, and finished-job cleanup retains checkpoints, exports,
receipts and all budget evidence.

`qualification_feeder.py` refills the context owner when fewer than 40 candidates
are ready for classification or fetching, aiming for 80. Delegated profile waits
do not consume the website crawler's refill capacity. It reads saved Maps/organic receipts, verified public
profiles' advertised websites and relevant observed links from qualified tutors
or local subject-matching directories. Search location and directory headings
are seed hints; every child still needs its own identity, subjects and locality.
No new search request or SDK turn is submitted by the feeder.

SQLite records each admission, source proof, parent/root lineage and source yield.
Sources that produce subject-matching local tutor candidates receive priority,
with every tenth admission sampling a lower-ranked eligible offer. Existing owners,
failed-fetch holds and paid holds are preserved. Expansion stops at 15 new owners
per parent, 30 per root, two external hops, and 2,000 new owners shared with manual
website imports. The fifteen-minute watcher can refill after the directory
producer finishes. The original deadline and classification bank remain binding.
`status.json.continuousSeeding` reports admissions, held proofs, source failures,
remaining offers and model-qualified yield. An empty verified source pool is
reported as exhausted; it never authorises fresh paid searches or a new window.
Known directory indexes go directly to the registered profile worker, with at most
ten subject-matching local entrypoints per refill. Their pages are not counted as
individual tutors. Any one requested subject satisfies the subject policy;
locality remains a separate requirement. Prior subject-policy receipts still
replay exactly, and no settled or uncertain decision is reset by a refill.

Delegated directory owners are deduplicated across parents and changed receipts,
so the same index cannot consume another expansion slot on the next refresh.
When the website queue remains below its low-water mark, the owner also prepares
`search-replenishment.json` and SQLite's `qualification_search_backlog`. This
backlog contains at most 200 unsubmitted, subject-matching Maps or organic queries
from the existing search plan, including descendants with verified parent
lineage. Queries retain approved locations and receive priority from recorded
tutor yield. All previously submitted outcomes, including failed and uncertain
requests, remain excluded. The backlog cannot dispatch requests and does not
keep an empty owner spinning. `lastRefill.websiteQueue`, `nextAction` and
`searchReplenishment` distinguish pending crawl work from a need for new sources
or a separately authorised paid-search window. Budget and stop time do not change.

`public_rosters.py` reads the cached Marlow Mums tutor article under
`public-rosters/`. Its canonical URL and SHA256 receipt must replay exactly.
Each named entry owns only its own paragraphs, links and public contact fields;
the article's town heading and neighbouring tutors cannot supply qualification.
Duplicated responsive markup is collapsed, while conflicting entries are held.
Only an explicit own local offer in the requested subjects reaches Decisions.
The registered qualification owner uses its existing exclusive locks, questions,
deadline and USD10 classification bank. Emails and phones stay outside model
input. `exports/public-roster-qualification/` and SQLite retain accepted, known
and held research entries separately from reviewed contacts and website profiles.
An article shared by several tutors is provenance, not a shared person identity.
Current availability and consent remain unverified; all entries remain ineligible
for outreach. No additional source fetch or search is performed by this reader.

Owned directory context also supports UKTutors, TutorDex, TutorExtra and
FindTutors adverts and public profiles. Newly supported pages require a matching
canonical URL as well as the successful body-hash receipt. FindTutors advert IDs
are checked against the advert route separately from the tutor's person ID;
public profile names, subjects and class locations come from the owner's fields.
Navigation and recommended tutors cannot supply those fields. Structured context
with changed own fields can trigger a new cached Decisions request for a settled,
local subject-matching candidate. This preserves the original receipt and never
requeues an uncertain paid request. Unavailable, conflicting, institutional and
music-only offers remain held or excluded. Publication replays both existing
profile context and the new source contracts.
Tutorperch, Tuitionlist, TeachTutti and Apprentus use the same receipt and
canonical-page checks with their existing own-field parsers. Apprentus supplies
the named advert owner's public handle without opening private profiles. A
directory index's full route must identify Bucks; nearby place-name collisions
such as Burnham-on-Sea, Wendover in Utah and Towcester do not enter its crawl
queue. This admission rule is a seed filter, not proof of any tutor's locality.

`keyword_expansion.py` keeps a shared `state/keyword-search.sqlite` alongside the separate paid
ledger. It generates plain location searches such as `11+ tutor Amersham`, `tutor Amersham`,
`11 plus tutor High Wycombe` and subject variants for both Google Maps and organic search.
The approved location list fixes the geographic scope. The research loop is:

1. Describe the lead target and seed service phrases across approved locations.
2. Search Maps and organic listings through the existing budgeted runner.
3. Cache outcomes and queue the returned public pages for Codex or Claude to classify.
4. Ask the agent to think of new phrases that would find more leads for that target.
5. Store those phrases as child queries, feed them to the live budgeted runner, and repeat.

Agents can propose customer wording, alternative role names, service specialisms and combinations,
such as `KS2 home tutor`, `grammar school preparation tuition` and `dyslexia specialist tutor`.
This is broader than replacing words in a fixed template. Proposals carry a reason and keep a
target anchor such as `tutor` or `tuition`. Service phrases omit locations and search operators;
the parent supplies the approved town and coordinates. Existing operator tactics run separately.
The agent treats page text as evidence, never instructions.

SDK duplicate checks use `lookup_known_leads.py` against the saved TSV index. Each batch can check twelve literal names, domains or phones; every query scans the full index and returns an exact match count with two bounded examples. This avoids combined `rg -m` searches where one frequently occurring lead exhausts the match limit before other names are checked. Lookup errors leave absence unproven. These matches guide research, while the final identity audit still controls contact acceptance.

The target, locations and recursion limits are saved as an immutable configuration. Defaults are
three generations and fifteen child queries per parent, counting Maps and organic separately.
Each query retains its first root and depth, with parent edges and expansion reasons. Re-imports
cannot promote descendants to fresh roots. Repeated phrases are deduplicated, and back-links
into earlier generations are rejected. Eight allowlisted service topics in verified receipts
also propose children automatically. Wider phrase ideation comes from the agent's structured
response, not untrusted page instructions.

The SQLite tables hold queries and their origins, exact paid-request observations, returned
URLs and receipt attribution, and classifier claims/results. Normalised search identity removes
case/spacing differences and equates a literal `11+` with its required `%2B` wire encoding.
Maps and organic remain distinct; depth and location settings remain part of the identity.
Submitted, successful, failed, empty and uncertain outcomes all block another equivalent search.
The original `paid.sqlite` remains the sole authority for charges and reservations.

New expansion plans put plain keyword searches first, remove already submitted searches using
the shared index and prioritise pending recursive expansions. Preparation freezes the resulting
plan and its SHA256. Existing manifests and deadlines remain unchanged. During a new run the
index observes paid receipts at export time; the fifteen-minute expansion watcher also syncs an
existing shared index, including the current stage without changing its immutable plan.
Adaptive runs refresh pending descendants every minute through a read-only connection. Each
child needs a verified successful parent, an intact root and generation chain, the configured
branch limit, and the exact approved town, coordinates and provider payload. All observed
outcomes block repeat spending. Current-run receipts remain attributable to their recursive
tactic. A corrupt child is held individually; an unavailable index holds the optional queue
while the frozen plan continues. These supplements share the existing ledger and paid cutoff.

The Buckinghamshire council directory renders its service pages from a public JSON API.
Reviewed `council_advert` records use the complete active service object from a cached response
of at most one hundred records. Replay verifies the HTTP receipt and raw body hashes, unique
service ID, advertised locality, tuition and contact route. Each export links to that service's
public page and retains the API receipt and record hash. Adjacent records, inactive services
and ambiguous IDs cannot supply a contact. This source does not call a paid provider.

TutorHunt's current profile header uses `profilenametxt` rather than an embedded heading.
The collector supports both layouts and requires the same profile's locality, subject table
and named information card. On restart, a versioned cache replay checks saved HTTP status,
receipt and body hashes before recovering profiles missed by the old parser. It records
progress every fifty pages without fetching again or resetting the page counter, queue or
deadline. Unavailable and outside-area adverts remain excluded from qualified exports.

From the runtime repository:

```bash
python3 agent-loops/tutor-lead-pathways/keyword_expansion.py --db state/keyword-search.sqlite seed \
  --towns state/tutor-lead-pathways-20261012-expansion-1/towns.json
python3 agent-loops/tutor-lead-pathways/keyword_expansion.py --db state/keyword-search.sqlite sync \
  --run-dir state/tutor-lead-pathways-20261012-expansion-1 \
  --towns state/tutor-lead-pathways-20261012-expansion-1/towns.json
python3 agent-loops/tutor-lead-pathways/keyword_expansion.py --db state/keyword-search.sqlite plan \
  --limit 200 --output state/tutor-studio-dispatch-20261012/next-keyword-plan.json
python3 agent-loops/tutor-lead-pathways/keyword_expansion.py --db state/keyword-search.sqlite claim \
  --worker claude --limit 10 --output state/tutor-studio-dispatch-20261012/classification-batch.json
python3 agent-loops/tutor-lead-pathways/keyword_expansion.py --db state/keyword-search.sqlite searches \
  --town Amersham --state searched --output state/tutor-studio-dispatch-20261012/amersham-searches.json
```

These commands do not call paid APIs. A plan export is a proposal for a separately prepared,
budgeted run. Sync all historical paid run directories before exporting it. Sync opens source
ledgers read-only and requires receipt SHA256, payload and task agreement before enqueueing URLs.
Tracking parameters do not create extra classifier jobs. An interrupted receipt import resumes
without losing search memory. Receipt errors remain visible and never authorise repeat spending.
Search-engine navigation and query-refinement links are excluded from the agent queue, while
public tutor websites hosted on Google Sites remain eligible. `exclude-navigation` marks earlier
pending navigation jobs as excluded errors and retains their original receipt provenance.

Codex or Claude can claim batches with a worker name, lease token and receipt-backed provenance.
SQLite atomically prevents two workers claiming the same URL. Claims expire after fifteen minutes
by default, with at most three attempts. Stale tokens cannot complete a replacement worker's claim.
Open the actual public pages or inspect existing crawl evidence, then save a classification JSON:

```json
{
  "kind": "uncertain",
  "sourceUrls": ["https://example.org/tutor"],
  "notes": "Needs the tutor's own locality and service evidence",
  "outreachEligible": false,
  "searchPhrases": [
    {"phrase": "grammar school preparation tuition", "reason": "Another way families describe entrance preparation"},
    {"phrase": "KS2 home tutor", "reason": "Find primary tutors advertising the curriculum and home visits"}
  ]
}
```

Allowed kinds are `independent_tutor`, `directory`, `institution`, `irrelevant` and `uncertain`.
Complete each claim with `complete --url-key KEY --worker claude --lease-token TOKEN --result FILE`.
Completion atomically saves classification and child queries. Each claim contains `researchBrief`
with the target and limits, plus `searchParents`. If several searches returned a URL, choose a
specific parent with `expansionParentQueryKey`; otherwise the deepest recorded parent is used.
An expired lease or invalid phrase rolls back both classification and expansion.

An agent can also propose phrases directly beneath a verified successful query:

```bash
python3 agent-loops/tutor-lead-pathways/keyword_expansion.py --db state/keyword-search.sqlite expand \
  --parent-query-key KEY --worker codex --ideas state/phrase-ideas.json --source-url https://example.org/tutor
```

`phrase-ideas.json` is an array of `phrase`/`reason` objects, at most twelve. The optional source
URL must belong to the parent's verified receipt. Failed and uncertain searches cannot expand.
For a different lead target, use a separate database and `seed --target "Independent bookkeepers"
--anchor bookkeeper --anchor accountant --terms FILE --towns FILE`. The terms file is a JSON
array of seed service phrases. No provider or model API key is needed for these commands;
native Codex/Claude sessions supply the reasoning and the existing runner owns paid execution.

Use `status` to see generations, parent edges, searched outcomes and pending/claimed/classified/error counts. Classification
is research triage, never audited tutor acceptance or permission to contact someone. Keep final
identity/locality verification and duplicate review in the existing audit pipeline.

Recursive service phrases have separate exploration counters shared across towns and search lanes. Three searches for one phrase do not test another phrase. The same normalised phrase identity drives feedback and selection; provider payloads, request keys, lineage and budget limits remain unchanged.

The later one-thousand-tutor target covers private tutors across subjects, excluding institutions. `directory_profiles.py` adds a separate public-profile stage alongside the existing Go/Maps research. It follows observed county/town links and pagination on FindTutors and public Tutorperch profiles, obeys robots rules, spaces host requests, bounds pages and duration, and caches both successful and failed GET observations by SHA256. It reads the named tutor's advertised tuition locality, never the platform's registered office or the search heading.

Childcare.co.uk supplies another bounded source: its Buckinghamshire private-tutor index, numeric pagination and public profile headers. The parser requires the person's header to say `Private Tutor in ...` and takes subjects from positive tutor summary tags. It does not follow login, chat, reviews or parent searches. A live Edinburgh profile carried a Buckinghamshire breadcrumb, so `directoryCountyCategory` is retained only for audit and never establishes locality. Profiles advertising the former Bucks districts Aylesbury Vale, Chiltern, South Bucks or Wycombe use the district names recognised in [Buckinghamshire Council's authority overview](https://www.buckinghamshire.gov.uk/planning-building-and-environment/policy/view-the-authority-monitoring-report-amr/authority-monitoring-report-2023-to-2024/overview-of-authority-area/). Wavendon, Upper Weald and Holmer Green were checked against Postcodes.io's OS Open Names place records; cached observations remain in the private run review folder.

The identity audit can use a complete name from FindTutors' own advert name field or Childcare's own profile header when a linked professional profile name is absent. It first replays the hashed page and checks the identity, locality and subjects. Initials, single names and service or business labels do not supply complete names. Different complete own names can resolve a first-name collision; exact name matches and unresolved first-name records remain held. Historical pages in the frozen baseline still cannot count as additional contacts.

TeachTutti subject lists provide observed profile seeds using each card's own place and complete subject labels. All numbered card classes are supported. The collector follows local cards and the current listing only, without expanding into nationwide related profiles, login, messages or search endpoints. Profiles require their own name/location header, named welcome section and subject table. Conflicting biography locality is held, and each public profile retains its own URL identity. Cached public-source receipts can be reused by the normal profile crawler without refetching. The same locality, subject and baseline audit applies before counting any contact.

Reviewed own-site adverts can also use browser-rendered text when the HTTP crawler receives a JavaScript shell. Save the complete observed advert and a SHA256 receipt with `observationType: browser_rendered_text`, source URL and observation time under the campaign public-source cache. The replay requires the complete unchanged text, preserves its browser provenance and rejects an HTTP status claim. Normal name, locality, subjects, contact route, baseline and duplicate checks still apply. This observation never implies availability or permission to contact someone.

Page selection rotates across queued hosts and prefers named profile pages within each host. The last host is checkpointed, so a resume retains the rotation. This prevents a new source's profiles from waiting behind a large existing directory frontier. A frozen 32-page queue comparison records page mix separately from actual tutor yield; the original time, page and request-spacing limits still apply.

The local-place set also covers verified Bucks villages and Milton Keynes neighbourhoods, including Hodge Lea, Fishermead, Kents Hill, Hanslope, Wingrave, Seer Green and Steeple Claydon. These additions require exact OS Open Names matches through Postcodes.io whose county/unitary authorities are entirely Bucks or Milton Keynes. Names with outside matches, such as Westcroft, Crownhill and Brill, remain unresolved without explicit county context; missing exact matches also remain unresolved. Public town indexes supply entry points, while a person's own profile still supplies locality evidence. Academic subject categories include art, anatomy, theology, Polish and general after-school/secondary tuition. This does not admit martial arts, therapy, life coaching or institutions.

After pending person pages, an unseen FindTutors town root takes precedence over further subject indexes and pagination. The county index did not lead into Milton Keynes' town root, so this gives newly seeded towns a timely discovery slot. A frozen queue comparison measures selection delay while preserving every page and host rotation. It does not estimate live tutor yield or alter any qualification, page, deadline or request-spacing limit.

TutorDex and TutorExtra add public town indexes and numeric profile identities. TutorDex requires the person's `Private ... Tutor in ...` header, with subject and town links inside that header. TutorExtra reads the person's name and role, the primary advertised location, and the subject table; global navigation and the advert title do not supply the name or subjects. Conflicting additional locations and explicit education-company or centre adverts are held. Only public town/subject indexes and exact profile paths are admitted, with private forums, APIs, messaging and reviews excluded. TutorExtra's High Wycombe sample included Reading and London results, which remain outside the local cohort.

TutorExtra cards with an explicit local town and tutor or teacher role can enter the fetch queue even when the card omits academic subjects. This defers subject review to the person's own profile. Institutions, unavailable and non-tuition cards stay excluded; a generic role never qualifies a contact. Saved listing pages can supply the same profile URLs through hash-bound seed batches without fetching the listing again or resetting counters.

UK Tutors supplies public numeric profile paths and observed town indexes. The county page's random selection included Worthing and Wembley, so its heading never qualifies a person. The parser requires the named person's own town header, matching information heading and subject block. URL towns only guide which profiles to inspect. Labels such as `11+ (secondary)` describe ages and do not count as 11+ entrance-exam subjects. The CGI search path, accounts and contact unlocks are excluded. Its public sitemaps contained town indexes rather than person URLs, so they were used only to identify observed entry points. Cached HTML decoding respects supported declared charsets, preserving names and smart punctuation on legacy pages. A follow-on stage replays inherited pages with the current decoder and parser while preserving its parent unchanged.

Additional rural locations were checked against cached Postcodes.io OS Open Names records: Flackwell Heath, Bledlow Ridge, Hughenden Valley, Little Kimble, Long Crendon, Stewkley, Stokenchurch and Woburn Sands. Bare Bourne End and Lane End have exact namesakes outside the county and remain unresolved. Merging a person's adverts preserves every page receipt and any conflicting location observed on a later advert.

The scope audit also checks the tutor's own advert title and current first-person location statements. Explicit conflicting locations require review even when a platform header names a local town. Therapy and other non-tuition adverts cannot qualify through a broad subject tag; an individual with a separate academic tuition advert can still qualify. Online breadcrumbs never turn a subject name into a location. Cheddington, Far Bletchley, New Bradwell, Bow Brickhill and Little Marlow were added only after exact OS place matches established the county. Bare Halton, Wing and Calverton remain ambiguous. Parser upgrades replay hash-verified cached pages to correct existing records without another fetch and retain the original stage deadline and allowances.

FindTutors adverts share their public platform tutor ID, so multiple subject adverts become one profile identity. Tutorperch uses its exact named profile path. The CSV retains source links, subjects and the evidence type. Advertised locality does not verify residence, current availability or consent. Cross-platform matches still require review; these counts must not be added to existing practice totals without deduplication. Institutions, chains and non-tuition lessons are held. The stage adds no provider charges, accounts, purchases, messages or tenant imports. All spending remains within the user's USD100 total cap.

`audit_directory_identities.py` replays the cached person pages and verifies each receipt/body SHA256 before counting the qualified export. It retains stable source identities, accepts reviewed merges only with matching evidence from each member, and holds uncertain matching names in a separate file. Matching first names with different complete professional names from their own profile links stay separate. An incomplete-name profile can match both of those people, but cannot collapse them into one count. Each possible-match group retains a deterministic subset whose members do not collide with each other, preferring complete own professional names; the remaining uncertain records stay held. A first-name collision is a review queue, not a verified merge. The audit writes `tutors.csv`, `tutors.json`, held records and counts with the input and decision hashes. It performs no network calls and grants no outreach eligibility. Use `--run-dir`, `--output-dir` and optional `--decisions` to run it against a frozen stage snapshot.

The final audit also holds bare Burnham, Haddenham, Penn, Sands, Bourton and Denham claims. Cached exact Postcodes.io OS place records show UK namesakes outside the county, or no Bucks match for bare Bourton; a directory heading cannot resolve them. Explicit Buckinghamshire context in the person's own locality remains usable. The 11+ flag requires explicit wording in the person's service fields or own advert heading and excludes headings about merely having passed or sat that exam. General entrance exams and primary ages do not imply an 11+ offering.

The finite three-hour stage is registered as `bucks-private-tutors-20261011`. Install it through the canonical registry-driven installer on Mac Studio. Private state lives in `state/bucks-private-tutors-20261011`; resuming keeps its original deadline and page allowance. A stage ending below the target does not complete the user's discovery goal. Focused checks are `python3 -m unittest discover -s tests -p test_directory_profiles.py`.

When a sealed stage still leaves fewer than one thousand identities after review, `prepare_profile_followon.py` can prepare a separate bounded stage. It requires the parent's terminal state and released owner lock, verifies cached receipts and bodies, preserves the parent files and records their hashes. The child retains the old seen/frontier/attempt state and an explicit additional-attempt allowance. Cached failures remain terminal. Its new job, window and provenance are separate; re-preparation cannot overwrite it. The first follow-on is registered as `bucks-private-tutors-20261011-extension-1`, with a separate fifteen-minute watcher, a two-hour window, zero provider budget and at most two thousand additional attempts. Its source-profile target is 1500 to leave room for unresolved matches. Only the evidence audit determines whether the one-thousand-tutor goal is reached.

Discovery improvements follow the bounded baseline/change/evaluate loop in [program.md](program.md). Offline seed experiments use frozen, SHA256-bound fetch outcomes and [evaluate-seeds.py](evaluate-seeds.py); private `results.tsv` files record kept, discarded and failed experiments. Queue efficiency and live candidate yield are reported separately.

Six-hour research job for independent 11+ and primary tutor practices in Buckinghamshire, including Milton Keynes. MacBook is the authoring source; Mac Studio owns the registered runtime.

The paths are Google Maps searches, Business Listings category searches, targeted organic search, community/professional directory and newsletter search, and a Codex TypeScript SDK session using existing ChatGPT OAuth. The SDK uses live web search, a read-only sandbox and structured source-attributed results. It cannot inherit provider/API credentials or enabled MCP servers. OAuth token usage is recorded; no dollar cost or remaining subscription allowance is inferred from token counts.

SDK turns execute through existing loopback SSH authentication on Studio, with strict host-key verification against the installed public host key. This avoids observed native file-open stalls when launchd reads Desktop-linked global instructions and skills. OAuth remains in the existing Codex credential store. No authentication settings or authorised SSH keys are changed.

All DataForSEO paths share one SQLite paid-request ledger. SHA256 identity includes endpoint and complete semantic payload but excludes the tracking tag. Existing provider receipts are imported before launch. Submitting requests become uncertain after a crash unless their durable receipt proves settlement. Failed and uncertain requests are never reposted. Receipt cost must agree between task and envelope. Request reservations and cent-rounded charges share a USD20 incremental ceiling, within the original USD100 discovery cap. A new depth-100 Maps query differs from an old depth-10 query and expands the result set intentionally.

`prepare.py` writes the immutable plan and manifest. The first actual runtime creates one fixed six-hour start/deadline; paid research stops fifteen minutes before the deadline to allow final cleaning. Resuming retains that window, budget and plan digest. The four script paths are interleaved and paced independently. Repeated provider failures cool down the affected path rather than replaying a request. The separately authorised twelve-hour continuation is described in [CAMPAIGN-12H.md](CAMPAIGN-12H.md).

A verified search-engine error on a grouped directory query can produce one different single-source query. Its own semantic hash, paid reservation and receipt remain in the shared ledger, with the original key recorded in runtime state. An uncertain response never triggers this fallback. Billed searches with no matches remain cached terminal outcomes and do not count as provider outages.

Eight observed Business Listings regions reported more than the base plan's two hundred results. Additional pages are derived only from saved successful receipts which prove more results exist. Pagination retains the same category, radius and hundred-result limit, caps each region at five hundred results and the window at twenty supplementary pages. Each page has its own semantic hash and parent key; cached failed or uncertain pages are never replayed. The original plan, shared USD20 budget and original deadline stay unchanged. Base pages whose preceding receipt does not establish further results are recorded as skipped without wasting another four-minute query slot.

New plans include unrestricted `tutor <town>` and `11+ tutor <town>` entry points for every town, on both Maps and organic search, before the more selective operator queries. DataForSEO requires `%2B` for a literal plus sign. Returned tutor websites enter the reviewed Go seed queue with their search and receipt attribution. The 44-town plan contains 616 entries, including 176 plain town searches. It remains paced and budgeted, so the fixed window can end with queries still unsubmitted.

Newly prepared manifests use two external-link steps, fifteen additional sites per root branch, eight pages per site, fifty sites and four hundred attempted pages per batch, four workers, and a ten-thousand-page total ceiling. `crawlLimits` records these choices in the immutable manifest. Manifests without that field retain the earlier depth-one, four-page/site, two-hundred-page batch behaviour and their original page budget. The reviewed Go ceilings still apply. Increasing the total allowance alone would not have changed the completed run: 275 site entries hit four scheduled pages, but no batch reached its total page cap.

Unfinished frontiers resume their existing checkpoints before new roots, retaining their parent links, depth, roots and per-site counters. The remaining global allowance bounds any increase in attempted pages. A site already fetched as an external descendant is not promoted to a new root that would reset the recursion limits. Historical page evidence is reused; no-website business listings remain available for later enrichment.

For a newly authorised run with completed historical evidence roots and a larger per-site allowance, earlier jobs' sites can be revisited within the new limits. Otherwise the old four-page/site history would prevent the expanded profile from inspecting those practices. Sites fetched within the current job still keep their lineage and cannot be promoted to fresh roots. Recorded Bucks listing addresses prioritise seed selection before unresolved and recorded outside addresses, without changing qualification or discarding unresolved leads.

Each paid primary or fallback call checks the original discovery cutoff immediately before admission. A primary response arriving after the cutoff cannot authorise a new fallback request. Resuming also validates the original five-hour-forty-five-minute paid window, independently of the six-hour final deadline.

Preparation also imports settled receipts from completed pathway and town-search runs, including failed searches, using their original semantic hashes. It verifies saved hashes, request/task identities and charges, refuses active or uncertain history, and carries previous exposure into the shared budget without charging imported receipts again. Cached entries are recorded as reused and do not consume another paced provider slot. Parallel and inherited exposure floors are retained. The completed 11 October run, its files and original limits remain unchanged.

New manifests also retain completed pathway evidence roots. Prior Go pages, Codex results and manual candidates enter the next export with historical provenance, original page observation times and verification notes. Historical pages and contacts are excluded from new-result counts. Paid receipt reuse alone would have lost those additional findings from the next lead index. Reuse does not establish current activity or consent.

New manifests bind their owner and monitor labels to a unique run identity and use fifteen-minute checks. The installer and watcher share the same validated configuration, so recovery and terminal unloading address that run. Legacy manifests retain their saved thirty-minute interval and labels. Each check records the actual owner lock/PID, checkpoint age, meaningful progress, paid usage, SDK usage and runtime errors. A missing owner can request at most three registered launchd restarts without killing another process. The monitor never calls a paid provider, saves its final report and unloads after completion or the original deadline.

New Codex research turns use isolated conversations and rotate four towns, wrapping the town list. This avoids reloading growing previous conversations. The shared, regenerated lead index supplies deduplication through targeted searches capped at eight matches. Existing jobs retain their saved thread mode. New jobs allow at most six turns with a one-million-token pre-start ceiling; a turn can exceed that ceiling, and recorded usage remains the authority. These limits do not imply subscription quota or a dollar price.

The launcher resolves only the two DataForSEO references through the existing `lib/op-cli.sh` wrapper. Its twenty-second calls isolate the CLI from macOS desktop settings and bound the whole process group; one cache-disabled retry has a fifty-second outer bound. The launcher removes the service account and unrelated API keys before executing the runner. SDK and crawler children receive only ordinary host environment fields, never provider credentials. This avoids the observed launchd/TCC startup hang.

Install only through the canonical registry-driven installer on Mac Studio:

```bash
scripts/install-launchd-tasks.sh --task tutor-lead-pathways-20261011
scripts/install-launchd-tasks.sh --task tutor-lead-pathways-watch-20261011
python3 agent-loops/tutor-lead-pathways/run.py status --run-dir state/tutor-lead-pathways-20261011
```

The separate evening run is registered as `tutor-lead-pathways-20261011-evening` and `tutor-lead-pathways-watch-20261011-evening`. Its installer receives the prepared folder name explicitly; it does not reuse the earlier job's window or artifacts.

Private artifacts live under `state/tutor-lead-pathways-20261011`: native paid receipts and hashes, SDK events/results/token usage, Go checkpoints/evidence, monitor reports, deduplicated business JSON/CSV, a filtered research CSV and final `REPORT.md`. Business identities and establishment IDs are separate. Advertised location, independent ownership, current subjects, email validity and consent remain explicit verification fields; no heuristics grant outreach eligibility. Source directories, jobs, schools and recognised chains are held. No emails, domain purchases or tenant imports are part of this job.

Final cleaning uses free cached Postcodes.io lookups for actual advertised establishment addresses. Buckinghamshire and Milton Keynes district codes determine listed county membership; HP/MK prefixes and search origins do not. A shared business with a Bucks branch survives even if its first listed address is in Berkshire. The exports separate county listings, local 11+/primary hints, unresolved locations, recorded outside addresses and held sources. Unverified provider email claims are retained separately from fetched-page email extraction. These lists remain research, with ownership and current tuition services requiring review.

`agent-researched-candidates.csv` gives a focused view of the SDK candidates, including professional profiles with no public postcode. It preserves locality/service claims, source links, activity caveats and held statuses. It does not add a location claim to the postcode-based county total or approve a candidate for outreach. Counts include previously known practices that received additional agent evidence.

Shared directory hosts retain their complete profile path as identity. Recognised individual Childcare.co.uk profiles remain research contacts; its generic search pages remain sources. Two independent profiles on one platform must not collapse into one business.

Bark profiles also retain their individual path. They stay held for source review, including the three profiles whose opened pages say the professional is currently unavailable. Their names, contact claims and verification notes remain separate.

Source-host matching also covers directory subdomains, including Cylex's town hosts and mobile Yelp. Yell, School Guide, Cylex and Yelp pages retain distinct paths and stay held for source review. Generic town search pages are not tutor businesses.

Email cleaning excludes known platform and template addresses. A checked example is [Natalie Frith Tutorials](https://nataliefrithtutorials.co.uk/): its home page includes `info@edublink.com` beside the tutor's own contact. The export retains the advertised tutor address, with its exact page attribution, while raw evidence still preserves the original page extraction.

Cleaning rechecks franchise and service scope even on earlier exports. Recognised franchises include [Kip McGrath](https://www.kipmcgrath.com/global/) and [NumberWorks'nWords](https://numberworksnwords.com/uk/own-a-business/franchise-opportunities). Listings explicitly categorised as museums, driving schools, martial arts, piano instruction, art schools, life coaching or translation services are held outside academic tuition scope. Primary school tutors and academic educational consultants remain available for review. CSV exports retain agent service/locality claims and verification notes, including historical adverts, parked domains and conflicting addresses. Current activity remains unverified.

The 11+ shortlist requires explicit 11+, 11-plus or eleven-plus wording in the saved page text/title or business name. A generic reference to grammar schools or entrance exams stays as an `admissionsHint` in page evidence and does not imply 11+ tuition. Text excerpts are bounded, so absent wording means the subject needs review. This refinement reuses saved pages without another fetch or paid request.

Crawler seeds consider the listing category and website as well as the name. A business categorised as a tutor is not skipped merely because its brand name has no tutoring keyword. Independent schools, colleges and preschools remain institutions rather than tutor leads. Personal trainers, music teachers, media companies, bag shops and golf clubs are held outside the academic scope. These filters do not discard an individual whose business name says primary school tutor.

Source review also confirmed franchise offerings from [Brain Up](https://www.brainup.uk/ukfranchise) and [Clara James Tutoring](https://clarajamestutoring.co.uk/tutoring-franchise/). Their names and known domains are held as recognised chains. A college word in a brand name alone is not an exclusion: [The Rye College](https://www.theryecollege.co.uk/) advertises private 11+ and primary tuition and is categorised as a tutor.

Facebook profile IDs remain in business identity, including mobile URLs and numbered people/pages routes. Tracking parameters do not merge two distinct profiles or split one known profile. PMT Education profile and directory paths remain distinct and held for source review. Its general contact-page emails must not be merged into a named tutor merely through their shared hosting domain.

Facebook groups and shared posts remain held discovery sources rather than named tutor businesses. Before launching a crawl batch, the controller calls the reviewed Go binary's offline `filter-seeds` command. This reuses the crawler's public URL and site admission rules. Rejected roots and reasons are saved separately; an entirely rejected batch does not launch a crawler or produce a missing-checkpoint failure. Permitted independent hosted sites still pass through the same site-identity rules.

County review found watercolour artists, recruiters, hypnotherapy and drama-school listings among broad Maps matches. Those explicit categories are held outside academic tuition. Teacher-training colleges and the provider's `Pre-school` category remain institutions. `Educational institution` alone is not an exclusion because genuine primary maths tutors also use that category.

Later private-tutor searches returned swimming schools, Pilates studios, retail businesses, accountants and other unrelated businesses. Explicit non-academic categories are held. Other categorised businesses need an academic category or a tuition identity in their name, website or attributed service claims; otherwise they are held as `needs_tuition_evidence`. The search keyword never proves the business offers tuition. Observed category aliases are merged before this weaker hold, so a corporate-office listing cannot hide a private-tutor listing for the same business. Individual tutors without websites remain available for review.

[ComputerXplorers](https://www.computerxplorers.co.uk/franchising) and [Code Ninjas](https://www.codeninjas.co.uk/faq) advertise franchise models and are held as recognised chains. Gendered secondary-school, community-school, faith-school and vocational-school categories remain institutions. The filters preserve primary school tutor businesses, language tutors, academic educational consultants and non-standard categories with an explicit tuition identity.

Named candidates found during manual source review live in private `review/candidates/*.json` files with the same lead fields as SDK results, plus an optional exact advertised address. They pass through the same business merging and cleaning. A shared directory URL stays in source attribution, never substitutes for a missing business website, so separate named tutors do not merge into one directory. Advertised emails remain unverified claims. Manual service wording can supply a subject hint, but never grants locality, independence, current activity or outreach eligibility. Historical adverts retain their dates in notes.

Verification:

```bash
python3 -m unittest discover -s tests -p test_tutor_pathways.py
node --test agent-loops/tutor-lead-pathways/agentic.test.mjs
```

## Additional Bucks tutors, 12 October 2026

The expansion uses `tactics.py` to prepare 1,320 distinct queries for 44 towns: subject-specific Maps searches, public-profile site operators, individual tutors' own websites, community/newsletter searches and unclaimed Business Listings. It reuses settled historic receipts before submitting anything. First Tutors queries are skipped because its own site served a closure notice during source review.

`prepare.py --strategy expansion` allocates only the original USD100 cap's remaining allowance. The new run imported 833 settled receipts and reserved at most USD88.74, with USD11.26 prior conservative exposure. `PaidCache` keeps one immutable allowance and serialises reservations. Unknown paid outcomes stay reserved and cannot be reposted. Existing jobs retain their saved budget, pacing, plan and deadline. New query intervals are fifteen seconds for Maps/organic/profile searches and sixty seconds for Business Listings; actual provider duration also bounds throughput.

`expansion_metrics.py` saves per-tactic successful, failed, uncertain and empty requests, newly observed result URLs, cost and URL yield every minute. Result URLs are a discovery measure, not verified people. `profile_seeds.py` publishes hash-bound public-source URL batches to the separate profile stage. `watch_expansion.py` replays profile evidence and compares accepted identities with the frozen delivered 1,010-record baseline every fifteen minutes. Potential baseline name matches stay held. SDK research uses the existing ChatGPT OAuth login, isolated streamed threads, attributed structured results and a recorded token ceiling. Its tokens do not establish dollar charges or subscription quota.

The campaign can prove a missing baseline full-name hint from cached FindTutors or Childcare profiles. Every frozen member and source must match its original page SHA256, successful receipt and owned complete name. A conflicting linked name or incomplete evidence keeps the match held. The overlay changes only the in-memory comparison; the baseline file, identities and aliases remain unchanged. Periodic audits and final CSV publication use the same proof, saved in `baseline-name-evidence.json`. Standalone comparisons accept `new_leads.py --profile-run-dir <cached-profile-run>`; they do not fetch pages or spend provider budget.

Apprentus support is limited to public lesson adverts and local maths, English and tutoring listings. A local URL is a seed hint only. Contact qualification requires the named advert owner's visible subjects and lesson location, replayed from its SHA256-bound page. The owned profile handle keeps one teacher's adverts together without fetching private `/in/` profiles or messaging routes. Worldwide recommendations and out-of-area listings are not followed. The registered crawler retains its robots checks, host spacing, original page allowance and deadline.

UK Tutors regional listings and Childcare town listings can seed the same public-profile queue. UK Tutors regional routes stay within the approved locality hints; Childcare accepts the supported subjects and at most the observed ten-mile route. Encoded Childcare town separators share one cache URL. Regional headings, search snippets and URL towns never qualify a person. Ambiguous Bourne End and Lane End names are permitted as campaign search hints only; their bare names do not change the locality evaluator. Previously archived provider receipts can publish these newly supported seeds without another paid request.

Tutorhunt adds public `/users/` profiles and observed local town/subject indexes. Qualification requires the person's own header, locality and subject table. Profiles explicitly marked unavailable are held. Contact forms, accounts, contact unlocks and messaging are excluded. The profile stage reuses 3,781 cached receipts, allows 5,000 additional public-page attempts and waits for provider seed batches within its own six-hour deadline. Go crawling retains depth two, fifteen additional sites per root branch, eight pages per site and the saved ten-thousand-page job ceiling.

All jobs and watchers are registered in `launchd-tasks/registry.json`; install through `scripts/install-launchd-tasks.sh --task <id>` on Mac Studio. The owner/health watcher checks actual process ownership and checkpoints. The seed/audit watcher never calls a paid provider. Keep the goal open until the audited delta proves at least one thousand additional identities. Keep the initial 187 late-arriving historical records separate from newly collected stage yield. Nothing in these artifacts authorises outreach.

The request-anchored twelve-hour continuation preserves the original deadline and remaining USD100 budget. Its next separately prepared classifier stage allows 4,000 URLs per directory, retaining the cumulative 10,000-attempt ceiling, eight pages per ordinary site, depth two and fifteen sites per branch. Existing jobs and frontier policies remain unchanged. A copied frontier retains prior attempts, lineage, decisions and access errors.

`source_health.py` reads those frontier databases without writing to them. Three native 403/429 failures hold a directory for paid-query selection as well as crawling. Queries exclusively targeting held sources are deferred, with their keys and reasons recorded, without a provider reservation or marking them searched. Accessible sources, free-text searches and mixed-source queries continue. Deferred lanes recheck within fifteen minutes; errors reading one frontier are recorded and do not stop other pathways. A source returning to an accessible state can be selected again. First Tutors' verified closure remains a permanent skip.

Hosted tutor websites use their tenant hostname for site identity. Separate Wix, Weebly, WordPress, Site123 and similar tenants each retain the ordinary eight-page allowance; links between tenants consume the existing external branch and depth limits. The transactional frontier migration preserves URL keys, roots, parents, attempts, evidence and paid decisions, rebuilds site yield, and holds pending pages if the corrected boundaries expose an existing recursion limit. Versioned seed batches reconsider URLs previously dropped by the shared-provider quota without repeating provider requests or admitted page attempts. Stop the classifier owner and its observer before deploying this migration to an active frontier. Capture the owner's loaded revision once at startup, separately from the prepared job revision.
