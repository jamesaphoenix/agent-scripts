# Tutor lead discovery through five pathways

Discovery improvements follow the bounded baseline/change/evaluate loop in [program.md](program.md). Offline seed experiments use frozen, SHA256-bound fetch outcomes and [evaluate-seeds.py](evaluate-seeds.py); private `results.tsv` files record kept, discarded and failed experiments. Queue efficiency and live candidate yield are reported separately.

Six-hour research job for independent 11+ and primary tutor practices in Buckinghamshire, including Milton Keynes. MacBook is the authoring source; Mac Studio owns the registered runtime.

The paths are Google Maps searches, Business Listings category searches, targeted organic search, community/professional directory and newsletter search, and a Codex TypeScript SDK session using existing ChatGPT OAuth. The SDK uses live web search, a read-only sandbox and structured source-attributed results. It cannot inherit provider/API credentials or enabled MCP servers. OAuth token usage is recorded; no dollar cost or remaining subscription allowance is inferred from token counts.

SDK turns execute through existing loopback SSH authentication on Studio, with strict host-key verification against the installed public host key. This avoids observed native file-open stalls when launchd reads Desktop-linked global instructions and skills. OAuth remains in the existing Codex credential store. No authentication settings or authorised SSH keys are changed.

All DataForSEO paths share one SQLite paid-request ledger. SHA256 identity includes endpoint and complete semantic payload but excludes the tracking tag. Existing provider receipts are imported before launch. Submitting requests become uncertain after a crash unless their durable receipt proves settlement. Failed and uncertain requests are never reposted. Receipt cost must agree between task and envelope. Request reservations and cent-rounded charges share a USD20 incremental ceiling, within the original USD100 discovery cap. A new depth-100 Maps query differs from an old depth-10 query and expands the result set intentionally.

`prepare.py` writes the immutable plan and manifest. The first actual runtime creates one fixed six-hour start/deadline; paid research stops fifteen minutes before the deadline to allow final cleaning. Resuming retains that window, budget and plan digest. The four script paths are interleaved and paced independently. Repeated provider failures cool down the affected path rather than replaying a request.

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
