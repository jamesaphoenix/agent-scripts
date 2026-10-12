# Tutor relevance frontier

The Go crawler fetches public pages with its existing robots, redirect, SSRF and response-size checks. The Python orchestrator chooses which page to inspect next. SQLite retains the queue, parent and root IDs, page evidence, source search IDs, decisions, errors and provider cost reservations.

## Selection

Each queued page has a lexical tutor cue, a location cue, its source's observed yield and a domain novelty flag:

`priority = 0.40 * tutorCue + 0.15 * locationCue + 0.25 * sourceYield + 0.10 * unseenDomain + 0.10 - 0.08 * externalDepth - 0.04 * uncertainHop`

Source yield uses `(successfulDomains + 1) / (evaluatedDomains + 2)`. A success needs an independent-tutor decision, local-service probability at least 0.70, and an approved place in the actual page text. Multiple pages from one domain cannot inflate yield. Fetch failures lower source priority but do not count as semantic evidence for pruning a branch. Every tenth selection explores an under-sampled source inside the admitted frontier.

This is a crawl scheduling heuristic. Probabilities have not been calibrated against a large labelled set. A website candidate is not an audited person, availability check or permission to contact someone.

## Expansion gates

- The Decisions API evaluates kind, academic tutoring relevance, local service, exclusively outside-area service and explicit 11+ provision in one shared-text request.
- Expand only independent tutors or directories with tutoring probability at least 0.75 and kind probability at least 0.65. Local evidence is needed before external expansion. A local directory seed may act as a bridge; every resulting tutor still needs its own locality evidence.
- Schools, franchises, unrelated pages and explicitly outside-area services stop. Ordinary education articles do not become tutor seeds just because they mention tuition.
- An ambiguous page with some tutoring evidence gets at most two internal contact/about/profile pages, one hop only. It cannot continue an uncertain chain or open another domain.
- External links need an explicit tutor cue. A known tutor directory may also supply a clearly labelled Website link; its destination must pass its own decision before expansion. Hosting, social sharing, software and generic resource links are not seeds.
- At most 15 additional domains per immediate parent, 30 per root, two external hops, six total link hops and eight scheduled pages per ordinary site. Known public tutor directories get at most 200 source pages so their individual profiles are usable. Re-importing a URL never resets its original lineage.
- Six distinct classified child domains with no local tutor yield stop the remaining recursive branch. A later seed import cannot reopen its counters.
- A run has a maximum of 5,000 page candidates, 2,000 visited sites and a frontier of at most four times its page limit. Its six-hour deadline and limits remain unchanged across restarts.

## Cost and recovery

`gpt-6-luna` on `/v1/decisions` costs USD0.10 per million input tokens, with no output-token charge under the standard rate. Text is bounded to 16 KB and requests to 64 KB. The SHA256 cache includes the model, prompt version, URL, extracted text and question contract. Successful identical requests are reused. Submitted or uncertain requests are held for reconciliation rather than reposted.

A new classification cache has a USD3 ceiling inside the original USD100 discovery allowance. The current paid discovery exposure of USD66.35 includes USD43 of uncertain DataForSEO reservations. Those holds are preserved. No new DataForSEO calls run in this stage. The cache persists cost in integer nanodollars and reserves a conservative full-request byte bound before submitting.

The owner and its fifteen-minute watcher are registered Studio launchd tasks. They survive laptop closure. A single filesystem lock fences the owner. Each Go fetch has a stable checkpoint and receives no API keys. Known directories use the existing research link-audit command because Go contact discovery excludes marketplace domains. It also performs at most one destination link check per directory page; this is free HTTP work and does not submit paid search requests. The watcher records actual launchd state and exports candidates. It restarts an idle owner with the same deadline. On terminal completion the owner removes its task-owned fetch scratch after preserving evidence and decisions in SQLite. The watcher unloads the completed jobs. Shared ledgers, cached API results, reports and final exports remain.

## Commands

Run preparation from a pinned source checkout on Studio:

```sh
python3 agent-loops/tutor-lead-pathways/focused_run.py prepare \
  --run-dir state/tutor-focused-20261012-1 \
  --index state/keyword-search.sqlite \
  --cache state/tutor-decisions-cache.sqlite \
  --history state/tutor-lead-pathways-20261012-expansion-1 \
  --crawler ../tracelearn-outbound/bin/tutor-crawler-countywide \
  --source-sha <pushed-source-sha>
scripts/install-launchd-tasks.sh --task tutor-focused-20261012-1
scripts/install-launchd-tasks.sh --task tutor-focused-20261012-1-watch
```

Validation: `python3 -m unittest discover -s tests -p 'test_focused*'` and `python3 -m unittest discover -s tests -p test_decisions_classifier.py`. Ordinary tests inject provider responses. Explicit live canaries retain usage receipts in the same budget cache as the owner.

Reference: https://developers.openai.com/api/docs/guides/decisions
