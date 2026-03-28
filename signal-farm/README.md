# signal-farm

`signal-farm` is a novelty and idea-frequency farming substrate for AI, context engineering, and agentic engineering.

It treats providers as candidate generators and evaluates them against a shared KB/KG baseline stored in Postgres. The system is designed to support:

- daily signal farming
- novelty detection
- recurring-idea detection
- human review queues
- DB-backed replay and evals
- later bandit and RL policy optimization

See [specs/signal-farm.md](./specs/signal-farm.md) for the design source of truth.

## Layout

- `specs/` - project specification
- `programs/` - operator-facing program files
- `configs/` - topic, provider, budget, and prompt config
- `skills/` - repo-local agent skills
- `sql/` - initial Postgres schema scaffold
- `src/signal_farm/` - Python package and CLI scaffold

## Defaults

- Postgres is the system of record
- `pgvector` is used for embedding retrieval
- novelty is judged against the KB/KG, not provider-vs-provider
- QMD semantic search and regex/keyword search must both be used for KB comparison
