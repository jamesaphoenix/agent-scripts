# Tutor lead discovery through five pathways

Six-hour research job for independent 11+ and primary tutor practices in Buckinghamshire, including Milton Keynes. MacBook is the authoring source; Mac Studio owns the registered runtime.

The paths are Google Maps searches, Business Listings category searches, targeted organic search, community/professional directory and newsletter search, and a Codex TypeScript SDK session using existing ChatGPT OAuth. The SDK uses live web search, a read-only sandbox and structured source-attributed results. It cannot inherit provider/API credentials or enabled MCP servers. OAuth token usage is recorded; no dollar cost or remaining subscription allowance is inferred from token counts.

SDK turns execute through existing loopback SSH authentication on Studio, with strict host-key verification against the installed public host key. This avoids observed native file-open stalls when launchd reads Desktop-linked global instructions and skills. OAuth remains in the existing Codex credential store. No authentication settings or authorised SSH keys are changed.

All DataForSEO paths share one SQLite paid-request ledger. SHA256 identity includes endpoint and complete semantic payload but excludes the tracking tag. Existing provider receipts are imported before launch. Submitting requests become uncertain after a crash unless their durable receipt proves settlement. Failed and uncertain requests are never reposted. Receipt cost must agree between task and envelope. Request reservations and cent-rounded charges share a USD20 incremental ceiling, within the original USD100 discovery cap. A new depth-100 Maps query differs from an old depth-10 query and expands the result set intentionally.

`prepare.py` writes the immutable plan and manifest. The first actual runtime creates one fixed six-hour start/deadline; paid research stops fifteen minutes before the deadline to allow final cleaning. Resuming retains that window, budget and plan digest. The four script paths are interleaved and paced independently. Repeated provider failures cool down the affected path rather than replaying a request.

A verified search-engine error on a grouped directory query can produce one different single-source query. Its own semantic hash, paid reservation and receipt remain in the shared ledger, with the original key recorded in runtime state. An uncertain response never triggers this fallback. Billed searches with no matches remain cached terminal outcomes and do not count as provider outages.

The existing reviewed Go crawler enriches new website seeds in bounded batches: external depth one, fifteen sites per root branch, four pages per site, fifty sites and two hundred pages per batch, four workers. The new window has a total four-thousand-page ceiling. Historical page evidence is reused; no-website business listings remain available for later enrichment.

The watch task runs at startup and every thirty minutes. It checks the actual owner lock/PID, checkpoint age, meaningful progress, paid usage, SDK usage and runtime errors. A missing owner can request at most three registered launchd restarts without killing another process. It never calls a paid provider. The monitor saves its final report and unloads after completion or the original deadline.

The launcher resolves only the two DataForSEO references through the existing `lib/op-cli.sh` wrapper. Its twenty-second calls isolate the CLI from macOS desktop settings and bound the whole process group; one cache-disabled retry has a fifty-second outer bound. The launcher removes the service account and unrelated API keys before executing the runner. SDK and crawler children receive only ordinary host environment fields, never provider credentials. This avoids the observed launchd/TCC startup hang.

Install only through the canonical registry-driven installer on Mac Studio:

```bash
scripts/install-launchd-tasks.sh --task tutor-lead-pathways-20261011
scripts/install-launchd-tasks.sh --task tutor-lead-pathways-watch-20261011
python3 agent-loops/tutor-lead-pathways/run.py status --run-dir state/tutor-lead-pathways-20261011
```

Private artifacts live under `state/tutor-lead-pathways-20261011`: native paid receipts and hashes, SDK events/results/token usage, Go checkpoints/evidence, monitor reports, deduplicated business JSON/CSV, a filtered research CSV and final `REPORT.md`. Business identities and establishment IDs are separate. Advertised location, independent ownership, current subjects, email validity and consent remain explicit verification fields; no heuristics grant outreach eligibility. Source directories, jobs, schools and recognised chains are held. No emails, domain purchases or tenant imports are part of this job.

Final cleaning uses free cached Postcodes.io lookups for actual advertised establishment addresses. Buckinghamshire and Milton Keynes district codes determine listed county membership; HP/MK prefixes and search origins do not. A shared business with a Bucks branch survives even if its first listed address is in Berkshire. The exports separate county listings, local 11+/primary hints, unresolved locations, recorded outside addresses and held sources. Unverified provider email claims are retained separately from fetched-page email extraction. These lists remain research, with ownership and current tuition services requiring review.

`agent-researched-candidates.csv` gives a focused view of the SDK candidates, including professional profiles with no public postcode. It preserves locality/service claims, source links, activity caveats and held statuses. It does not add a location claim to the postcode-based county total or approve a candidate for outreach. Counts include previously known practices that received additional agent evidence.

Shared directory hosts retain their complete profile path as identity. Recognised individual Childcare.co.uk profiles remain research contacts; its generic search pages remain sources. Two independent profiles on one platform must not collapse into one business.

Bark profiles also retain their individual path. They stay held for source review, including the three profiles whose opened pages say the professional is currently unavailable. Their names, contact claims and verification notes remain separate.

Source-host matching also covers directory subdomains, including Cylex's town hosts and mobile Yelp. Yell, School Guide, Cylex and Yelp pages retain distinct paths and stay held for source review. Generic town search pages are not tutor businesses.

Email cleaning excludes known platform and template addresses. A checked example is [Natalie Frith Tutorials](https://nataliefrithtutorials.co.uk/): its home page includes `info@edublink.com` beside the tutor's own contact. The export retains the advertised tutor address, with its exact page attribution, while raw evidence still preserves the original page extraction.

Cleaning rechecks franchise and service scope even on earlier exports. Recognised franchises include [Kip McGrath](https://www.kipmcgrath.com/global/) and [NumberWorks'nWords](https://numberworksnwords.com/uk/own-a-business/franchise-opportunities). Listings explicitly categorised as museums, driving schools, martial arts, piano instruction, art schools, life coaching or translation services are held outside academic tuition scope. Primary school tutors and academic educational consultants remain available for review. CSV exports retain agent service/locality claims and verification notes, including historical adverts, parked domains and conflicting addresses. Current activity remains unverified.

The 11+ shortlist requires explicit 11+, 11-plus or eleven-plus wording in the saved page text/title or business name. A generic reference to grammar schools or entrance exams stays as an `admissionsHint` in page evidence and does not imply 11+ tuition. Text excerpts are bounded, so absent wording means the subject needs review. This refinement reuses saved pages without another fetch or paid request.

Verification:

```bash
python3 -m unittest discover -s tests -p test_tutor_pathways.py
node --test agent-loops/tutor-lead-pathways/agentic.test.mjs
```
