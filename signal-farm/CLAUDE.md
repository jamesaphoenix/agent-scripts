# signal-farm

`signal-farm` is a KB/KG-backed novelty and idea-frequency system.

## Working model

- Search providers surface candidates.
- The KB/KG determines novelty, recurrence, validation state, and usefulness.
- Human review happens through explicit queues, not a single digest feed.
- New ML and RL inputs should be attached as append-only feature records, not one-off schema hacks.
- The `programs/` files are the main human-editable control surface for autoresearch-style loops.
- When an LLM must return machine-consumed JSON, prefer structured outputs tied to an explicit schema.

## Retrieval rule

Before labeling novelty or frequency, build an evidence bundle from:

- QMD semantic search
- 3-5 regex or keyword searches
- graph neighborhood traversal
- vector neighbors
- recent item windows

## Operational rule

- Use DB-backed cassette replay for evals and golden collection whenever possible.
- Avoid provider-vs-provider novelty comparisons.