# Tutor lead discovery through five pathways

## Keyword expansion and upstream classification

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

The later one-thousand-tutor target covers private tutors across subjects, excluding institutions. `directory_profiles.py` adds a separate public-profile stage alongside the existing Go/Maps research. It follows observed county/town links and pagination on FindTutors and public Tutorperch profiles, obeys robots rules, spaces host requests, bounds pages and duration, and caches both successful and failed GET observations by SHA256. It reads the named tutor's advertised tuition locality, never the platform's registered office or the search heading.

Childcare.co.uk supplies another bounded source: its Buckinghamshire private-tutor index, numeric pagination and public profile headers. The parser requires the person's header to say `Private Tutor in ...` and takes subjects from positive tutor summary tags. It does not follow login, chat, reviews or parent searches. A live Edinburgh profile carried a Buckinghamshire breadcrumb, so `directoryCountyCategory` is retained only for audit and never establishes locality. Profiles advertising the former Bucks districts Aylesbury Vale, Chiltern, South Bucks or Wycombe use the district names recognised in [Buckinghamshire Council's authority overview](https://www.buckinghamshire.gov.uk/planning-building-and-environment/policy/view-the-authority-monitoring-report-amr/authority-monitoring-report-2023-to-2024/overview-of-authority-area/). Wavendon, Upper Weald and Holmer Green were checked against Postcodes.io's OS Open Names place records; cached observations remain in the private run review folder.

Page selection rotates across queued hosts and prefers named profile pages within each host. The last host is checkpointed, so a resume retains the rotation. This prevents a new source's profiles from waiting behind a large existing directory frontier. A frozen 32-page queue comparison records page mix separately from actual tutor yield; the original time, page and request-spacing limits still apply.

The local-place set also covers verified Bucks villages and Milton Keynes neighbourhoods, including Hodge Lea, Fishermead, Kents Hill, Hanslope, Wingrave, Seer Green and Steeple Claydon. These additions require exact OS Open Names matches through Postcodes.io whose county/unitary authorities are entirely Bucks or Milton Keynes. Names with outside matches, such as Westcroft, Crownhill and Brill, remain unresolved without explicit county context; missing exact matches also remain unresolved. Public town indexes supply entry points, while a person's own profile still supplies locality evidence. Academic subject categories include art, anatomy, theology, Polish and general after-school/secondary tuition. This does not admit martial arts, therapy, life coaching or institutions.

After pending person pages, an unseen FindTutors town root takes precedence over further subject indexes and pagination. The county index did not lead into Milton Keynes' town root, so this gives newly seeded towns a timely discovery slot. A frozen queue comparison measures selection delay while preserving every page and host rotation. It does not estimate live tutor yield or alter any qualification, page, deadline or request-spacing limit.

TutorDex and TutorExtra add public town indexes and numeric profile identities. TutorDex requires the person's `Private ... Tutor in ...` header, with subject and town links inside that header. TutorExtra reads the person's name and role, the primary advertised location, and the subject table; global navigation and the advert title do not supply the name or subjects. Conflicting additional locations and explicit education-company or centre adverts are held. Only public town/subject indexes and exact profile paths are admitted, with private forums, APIs, messaging and reviews excluded. TutorExtra's High Wycombe sample included Reading and London results, which remain outside the local cohort.

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

Tutorhunt adds public `/users/` profiles and observed local town/subject indexes. Qualification requires the person's own header, locality and subject table. Profiles explicitly marked unavailable are held. Contact forms, accounts, contact unlocks and messaging are excluded. The profile stage reuses 3,781 cached receipts, allows 5,000 additional public-page attempts and waits for provider seed batches within its own six-hour deadline. Go crawling retains depth two, fifteen additional sites per root branch, eight pages per site and the saved ten-thousand-page job ceiling.

All jobs and watchers are registered in `launchd-tasks/registry.json`; install through `scripts/install-launchd-tasks.sh --task <id>` on Mac Studio. The owner/health watcher checks actual process ownership and checkpoints. The seed/audit watcher never calls a paid provider. Keep the goal open until the audited delta proves at least one thousand additional identities. Keep the initial 187 late-arriving historical records separate from newly collected stage yield. Nothing in these artifacts authorises outreach.

The request-anchored twelve-hour continuation preserves the original deadline and remaining USD100 budget. Its next separately prepared classifier stage allows 4,000 URLs per directory, retaining the cumulative 10,000-attempt ceiling, eight pages per ordinary site, depth two and fifteen sites per branch. Existing jobs and frontier policies remain unchanged. A copied frontier retains prior attempts, lineage, decisions and access errors.

`source_health.py` reads those frontier databases without writing to them. Three native 403/429 failures hold a directory for paid-query selection as well as crawling. Queries exclusively targeting held sources are deferred, with their keys and reasons recorded, without a provider reservation or marking them searched. Accessible sources, free-text searches and mixed-source queries continue. Deferred lanes recheck within fifteen minutes; errors reading one frontier are recorded and do not stop other pathways. A source returning to an accessible state can be selected again. First Tutors' verified closure remains a permanent skip.

Hosted tutor websites use their tenant hostname for site identity. Separate Wix, Weebly, WordPress, Site123 and similar tenants each retain the ordinary eight-page allowance; links between tenants consume the existing external branch and depth limits. The transactional frontier migration preserves URL keys, roots, parents, attempts, evidence and paid decisions, rebuilds site yield, and holds pending pages if the corrected boundaries expose an existing recursion limit. Versioned seed batches reconsider URLs previously dropped by the shared-provider quota without repeating provider requests or admitted page attempts. Stop the classifier owner and its observer before deploying this migration to an active frontier. Capture the owner's loaded revision once at startup, separately from the prepared job revision.
