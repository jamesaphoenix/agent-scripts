# signal-farm

This project farms novelty and recurring ideas in AI, context engineering, and agentic engineering.

## Core rules

- Treat providers as candidate generators, not as novelty judges.
- Judge novelty and recurrence against the shared KB/KG in Postgres.
- For KB comparison, always run QMD semantic search and 3-5 regex or keyword searches in parallel.
- Keep pinned queries operationally enabled when configured, but exclude them from query-planning evals.
- Use Postgres for runs, cassettes, eval caches, labels, goldens, and policy telemetry.
- Attach new ML or RL features through append-only, namespaced feature records instead of one-off ad hoc schema drift.
- Treat `programs/` as the main human-editable control surface for autoresearch-style loops.
- When an LLM must return machine-consumed JSON, use structured outputs or schema-constrained modes instead of freeform JSON parsing.

## Source of truth

- Design: [specs/signal-farm.md](./specs/signal-farm.md)
- Operator program: [programs/daily-news-v1.md](./programs/daily-news-v1.md)
- Skills:
  - [.codex/skills/auto-research/SKILL.md](./.codex/skills/auto-research/SKILL.md)
  - [.codex/skills/novelty-farming/SKILL.md](./.codex/skills/novelty-farming/SKILL.md)
