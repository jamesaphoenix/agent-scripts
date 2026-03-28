# Daily News v1

## Goal

Farm high-signal items in AI, context engineering, and agentic engineering, then rank them by novelty, recurrence, authority, and usefulness.

## Operating rules

1. Run seeded, expanded, and pinned queries.
2. Keep pinned queries active, but exclude them from query-planning evals.
3. Normalize, dedupe, and link items into the KB/KG.
4. Build evidence bundles from:
   - QMD semantic search
   - 3-5 regex or keyword searches
   - graph neighborhood traversal
   - vector neighbors
5. Label novelty, idea frequency, content state, and review value.
6. Surface:
   - novel items worth reviewing now
   - emerging ideas worth watching
   - validated references worth promoting into the KB

## Output priorities

- Prefer authoritative sources over low-authority repetition.
- Prefer material updates over cosmetic churn.
- Surface recurring ideas even when they are not strictly novel.
- Avoid provider-vs-provider novelty comparisons.
