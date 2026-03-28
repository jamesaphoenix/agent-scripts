---
kind: spec
spec_type: design
doc_id: doc-f7be879de753
name: signal-farm-acquisition-and-evidence-design
title: "Signal Farm Acquisition and Evidence Design"
status: draft
version: 1
owners:
  - jamesaphoenix
summary: "Technical design for signal acquisition, fairness, retrieval policy, and evidence construction in signal-farm."
domain: signal-farm
tags:
  - design
  - signal-farm
  - acquisition
  - evidence
  - retrieval
depends_on: []
supersedes: []
implements: signal-farm-acquisition-and-evidence-prd
last_reviewed_at: 2026-03-28
---

# Plan

> Working plan: [/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md](/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md)

This design doc is the acquisition-and-evidence slice of the signal-farm decomposition. It preserves the acquisition, fairness, retrieval, and query-policy source text verbatim while adding subsystem-specific implementation guidance for future agents.

# Summary

Acquisition and evidence owns how `signal-farm` finds candidate signals, binds them to a stable knowledge baseline, and constructs the bounded evidence bundle that downstream novelty and recurrence logic depends on. This subsystem sits between operator policy inputs and the later storage and labeling subsystems, so it has to preserve provenance, determinism, and fairness from the start.

# Architecture
## System Context

This subsystem starts after the platform-core run orchestration hands off query planning and candidate acquisition. It depends on shared contracts from `packages/contracts`, provider adapters in `packages/providers`, and KB/KG retrieval surfaces exposed by storage-oriented repositories.

## Components

- Query planning logic that turns topics and program configuration into `QueryIntent` records.
- Provider and source adapters that execute `search`, `feed`, `curated`, `monitor`, and `pretraining_mining` strategies.
- Retrieval-policy logic that gathers semantic, lexical, graph, vector, and recent-window evidence.
- Performance windows for query, strategy, and provider scoring.
- Snapshot-binding rules that keep acquisition and evaluation on the same knowledge baseline.

## Flow of Control

The subsystem plans query intents, executes provider or source calls, normalizes each result into `CandidateItem`, and then builds an evidence bundle against a snapshot-bound KB/KG baseline. The result handed to downstream novelty and review subsystems is a candidate plus a deterministic evidence bundle and performance telemetry for the strategy that surfaced it.

# Interfaces
Use this section for implementation seams and boundary contracts. Add the subsections that fit the subsystem; do not collapse concrete design into one generic YAML list.

## Types

This subsystem owns `KnowledgeSnapshotRef`, `SnapshotAddressableRecord`, `RetrievalPolicy`, `EvidenceBundle`, `EvidenceItemRef`, `QueryIntent`, `QueryExecution`, and `CandidateItem`. The associated enums for topics, query classes, and acquisition strategies are shared contracts but are operationally rooted here.

## Context

Acquisition composes provider adapters, query-planning policies, retrieval-policy configuration, and storage-backed retrieval repositories under one Effect layer. The important runtime context is the tuple of topic window, knowledge snapshot, retrieval policy version, provider/strategy choice, and budget class.

## Repositories

This subsystem relies on query repositories for `QueryIntent` and `QueryExecution`, graph and canonical-item repositories for evidence retrieval, and performance-window persistence for query, strategy, and provider scoring. Snapshot filtering belongs at repository or query-plan boundaries, not in ad hoc in-memory post-processing.

## Services

The owning services are `AcquisitionService`, `NormalizationService`, `DedupeService`, and the evidence-building entrypoint on `NoveltyService`. Query planning and evidence construction must accept explicit policy versions and return durable, provenance-rich records.

## Workflows and Jobs

The main workflow is query planning followed by strategy-specific acquisition and snapshot-bound evidence retrieval. Monitoring, feed polling, and pinned queries are operational jobs, but they all converge onto the same candidate and evidence contracts.

## External Providers and Adapters

Provider adapters cover native web search, engine-backed search, feeds, monitors, and hypothesis generation sources. Adapters may differ in transport and payloads, but they must converge onto the same provenance and `CandidateItem` output contract.

## Events

The subsystem persists query-intent creation, query execution, candidate acquisition, and strategy/provider performance windows as its durable event-like outputs. Idempotency matters at query execution persistence because replay and operator retries must not double count performance windows.

## Endpoints (optional)

Any exposed routes should center on planning queries, executing acquisition, and inspecting evidence or performance windows. They should remain orchestration wrappers over shared services rather than separate retrieval implementations.

## Machine-readable Contract Appendix (optional)

The authoritative machine-readable contracts for this subsystem are the TypeScript interfaces preserved in the source text below. Additional generated artifacts should derive from those definitions rather than duplicating them by hand.

# Data Model

Acquisition and evidence owns durable query intents, query executions, candidate items, snapshot references, and retrieval-policy-versioned evidence bundles. These records form the traceable upstream context for later novelty, review, and replay workflows.

## Database Schema

The critical schema group for this subsystem is `query_intents`, `query_executions`, `query_performance_windows`, `strategy_performance_windows`, and `provider_performance_windows`. Candidate records and evidence references also need to retain provenance fields, snapshot identifiers, and retrieval-policy version keys.

## Stored State Shapes

Stored shapes include the operator's intent (`QueryIntent`), the measured execution (`QueryExecution`), the normalized candidate (`CandidateItem`), the bound knowledge snapshot, and the evidence bundle used for later judgment. The key semantic fields are strategy, provider, query class, exclusion from query eval, and the snapshot leakage boundary.

## Derived and Read Models

Derived views include rolling query, strategy, and provider performance windows plus inspection surfaces for evidence composition and candidate yield. These read models exist to compare strategies over time without mutating the original acquisition record.

## Indexes and Constraints

The subsystem needs unique execution identities, stable strategy and provider keys, and indexes over topic, query class, snapshot time, and performance windows. Retrieval behavior also depends on deterministic duplicate collapse rules and stable tie-break ordering.

## State Transitions and Lifecycles

Acquisition itself rides inside the platform run lifecycle, but this subsystem still owns the transition from planned query intent to executed query record to normalized candidate to evidence bundle. Once a candidate is handed off with its evidence bundle, the downstream labeling subsystem owns the next stage.

# Invariants
```yaml
invariants:
  - id: INV-SFAE-001
    statement: Every acquisition strategy normalizes into the same CandidateItem contract with provenance preserved.
    source_requirements:
      - REQ-SFAE-001
      - REQ-SFAE-002
  - id: INV-SFAE-002
    statement: Providers and strategies are judged against a shared KB/KG baseline instead of against one another.
    source_requirements:
      - REQ-SFAE-003
      - REQ-SFAE-004
  - id: INV-SFAE-003
    statement: Evidence bundles include both QMD semantic search and parallel lexical search before downstream judgment.
    source_requirements:
      - REQ-SFAE-005
  - id: INV-SFAE-004
    statement: Retrieval policy behavior is versioned, explicit, and deterministic enough for comparable evidence construction.
    source_requirements:
      - REQ-SFAE-006
  - id: INV-SFAE-005
    statement: Pinned queries remain operational inputs while excluded from query-planning evaluation.
    source_requirements:
      - REQ-SFAE-007
  - id: INV-SFAE-006
    statement: Query, strategy, and provider performance telemetry persists over time for later policy learning.
    source_requirements:
      - REQ-SFAE-008
      - REQ-SFAE-009
```

# Failure Modes
```yaml
failure_modes:
  - id: FM-SFAE-001
    condition: Providers are compared directly against each other instead of against the shared KB/KG baseline.
    impact: Fairness and provider-scoring conclusions become invalid.
    mitigation: Bind every run to a knowledge snapshot and common retrieval policy version.
  - id: FM-SFAE-002
    condition: QMD or lexical retrieval is skipped before novelty or recurrence judgment.
    impact: Evidence bundles become incomplete and false novelty rates increase.
    mitigation: Enforce retrieval-policy minimum channels and fail behavior at the service boundary.
  - id: FM-SFAE-003
    condition: Snapshot filtering uses mutable record timestamps instead of `knowledgeAvailableAt`.
    impact: Temporal leakage contaminates eval and replay comparisons.
    mitigation: Apply snapshot visibility rules directly in repository-backed retrieval queries.
  - id: FM-SFAE-004
    condition: Pinned queries contribute to query-planning evals.
    impact: Planning metrics are biased by reserved-budget operational inputs.
    mitigation: Persist `excludeFromQueryEval` and enforce it in eval cohort selection.
  - id: FM-SFAE-005
    condition: Machine-consumed query-expansion or evidence judgments use freeform parsing.
    impact: Downstream dedupe, scoring, or routing records become unstable.
    mitigation: Require schema-constrained outputs for machine-consumed acquisition and evidence calls.
```

# Verification
```yaml
verification:
  - id: VER-SFAE-001
    requirement_ids:
      - REQ-SFAE-001
      - REQ-SFAE-002
    invariant_ids:
      - INV-SFAE-001
    test: packages/core/test/acquisition/candidate-normalization.integration.test.ts
    assertion: All strategy adapters emit CandidateItem records with the required provenance fields populated.
  - id: VER-SFAE-002
    requirement_ids:
      - REQ-SFAE-003
      - REQ-SFAE-004
    invariant_ids:
      - INV-SFAE-002
    test: packages/core/test/acquisition/fairness-snapshot.integration.test.ts
    assertion: Runs with the same snapshot and policy compare providers only through the shared evidence baseline.
  - id: VER-SFAE-003
    requirement_ids:
      - REQ-SFAE-005
      - REQ-SFAE-006
    invariant_ids:
      - INV-SFAE-003
      - INV-SFAE-004
    test: packages/core/test/acquisition/evidence-bundle.integration.test.ts
    assertion: Evidence bundles combine semantic and lexical channels and follow deterministic merge and tie-break rules.
  - id: VER-SFAE-004
    requirement_ids:
      - REQ-SFAE-007
    invariant_ids:
      - INV-SFAE-005
    test: packages/core/test/acquisition/pinned-query-policy.integration.test.ts
    assertion: Pinned queries run operationally while remaining excluded from query-planning eval cohorts.
  - id: VER-SFAE-005
    requirement_ids:
      - REQ-SFAE-008
      - REQ-SFAE-009
    invariant_ids:
      - INV-SFAE-006
    test: packages/core/test/acquisition/performance-windows.integration.test.ts
    assertion: Query, strategy, and provider telemetry persists yield, cost, and duplicate-rate metrics over time.
```

# Testing Strategy
## Traceability Matrix

- `REQ-SFAE-001`, `REQ-SFAE-002`, `INV-SFAE-001` -> `packages/core/test/acquisition/candidate-normalization.integration.test.ts`
- `REQ-SFAE-003`, `REQ-SFAE-004`, `INV-SFAE-002` -> `packages/core/test/acquisition/fairness-snapshot.integration.test.ts`
- `REQ-SFAE-005`, `REQ-SFAE-006`, `INV-SFAE-003`, `INV-SFAE-004` -> `packages/core/test/acquisition/evidence-bundle.integration.test.ts`
- `REQ-SFAE-007`, `INV-SFAE-005` -> `packages/core/test/acquisition/pinned-query-policy.integration.test.ts`
- `REQ-SFAE-008`, `REQ-SFAE-009`, `INV-SFAE-006` -> `packages/core/test/acquisition/performance-windows.integration.test.ts`

## Integration Scenarios

- Execute one candidate-acquisition run per strategy family and assert all emit comparable `CandidateItem` contracts.
- Re-run the same acquisition against the same snapshot boundary and verify the evidence bundle remains materially comparable.
- Force QMD or lexical retrieval failure and verify fail mode follows the configured retrieval policy.
- Execute pinned and non-pinned queries together and assert only the non-pinned cohort contributes to planning eval metrics.
- Compare provider performance windows over multiple runs and assert the telemetry fields persist cost, yield, and duplicate-rate data.

## Test Files

- `packages/core/test/acquisition/candidate-normalization.integration.test.ts`
- `packages/core/test/acquisition/evidence-bundle.integration.test.ts`
- `packages/core/test/acquisition/fairness-snapshot.integration.test.ts`
- `packages/core/test/acquisition/performance-windows.integration.test.ts`
- `packages/core/test/acquisition/pinned-query-policy.integration.test.ts`
- `packages/providers/test/acquisition/provider-adapters.contract.test.ts`

# Source Text

The following source sections are copied verbatim from `specs/signal-farm.md` for subsystem fidelity during implementation.

## Signal acquisition strategies

`signal-farm` should treat signal acquisition as a strategy layer, not as a single search loop.

### Strategy families

The system should support these strategy families:

- `search`
  - native provider web search
  - engine-backed web search
  - X or social search where supported
- `feed`
  - RSS
  - Atom
  - changelog feeds
  - release feeds
  - newsletter ingestion when it can be normalized cleanly
- `curated`
  - manually maintained high-value source sets by topic
  - manually curated watchlists of sites, feeds, repos, docs, or authors
- `monitor`
  - direct polling of known pages
  - docs change monitoring
  - pricing page monitoring
  - GitHub release monitoring
  - known repo changelog monitoring
- `pretraining_mining`
  - experimental hypothesis generation lane
  - uses LLM prior knowledge to suggest weakly indexed or emerging themes
  - must only emit hypotheses that are then grounded via standard acquisition channels

### Acquisition invariants

- Every acquisition strategy must normalize into the same `CandidateItem` contract.
- Every candidate must retain acquisition provenance.
- Hypothesis-only strategies cannot directly create canonical truth.
- Acquisition strategies should be scored independently of providers.

### Acquisition metadata

Every candidate must retain:

- acquisition strategy
- strategy version
- acquisition adapter id
- provider id or source id
- originating query id, feed id, monitor id, or hypothesis id
- acquisition timestamp
- acquisition cost when applicable
- freshness class

## Fairness model

Providers must not be compared against each other for novelty.

The fairness model is:

- providers generate candidate items
- the KB/KG is the shared novelty and recurrence baseline
- each provider is judged against the same evidence-retrieval policy and labeling rubric

Fairness invariants:

- same topic window
- same retrieval policy version
- same novelty rubric
- same recurrence rubric
- same evidence-budget class
- same validation-state rules

### Snapshot isolation

Fairness depends on a stable knowledge baseline.

Every run and evaluation must bind to:

- `knowledge_snapshot_id`
- `snapshot_as_of`

The system must define whether a run uses:

- a named immutable snapshot
- or a deterministic `as_of` cut taken before acquisition starts

A provider or strategy evaluation must never see KB facts that were ingested after that run’s snapshot boundary.

### Snapshot contracts

```ts
export interface KnowledgeSnapshotRef {
  readonly knowledgeSnapshotId: string
  readonly snapshotAsOf: string
  readonly retrievalPolicyVersion: string
}
```

### Temporal knowledge model

All durable knowledge-bearing records must be append-only and snapshot-addressable.

This applies to:

- facts
- graph edges
- labels and judgments
- feature records

Corrections and re-judgments must create new rows or new versions, not mutate old rows in place.

Every persisted record that can participate in retrieval, scoring, replay, or eval should carry temporal fields that make snapshot filtering deterministic.

```ts
export interface SnapshotAddressableRecord {
  readonly createdAt: string
  readonly knowledgeAvailableAt: string
  readonly supersededAt: string | null
}
```

Evaluation rule:

- a run bound to `snapshotAsOf = T` may only retrieve or score records where `knowledgeAvailableAt <= T`
- if a record supports supersession, it is visible only when `supersededAt` is null or `supersededAt > T`

`createdAt` is not a substitute for `knowledgeAvailableAt`. The latter is the canonical leakage boundary for replay and eval.

## Retrieval and evidence bundle

Before any novelty or frequency judgment, build a bounded evidence bundle from the KB/KG.

Required retrieval modes:

- QMD semantic search
- 3-5 regex or keyword searches in parallel
- graph neighborhood traversal
- vector neighbors
- recent-window filtering

No novelty or recurrence classifier should compare an item to the entire corpus directly.

### Retrieval-policy requirements

The retrieval system must be deterministic enough that two implementations with the same inputs produce materially comparable evidence bundles.

Define a first-class retrieval policy contract with:

- per-channel top-k limits
- per-channel score normalization rules
- cross-channel merge rules
- tie-break ordering
- duplicate-collapse rules
- error handling behavior
- minimum evidence requirements for judgment

```ts
export interface RetrievalPolicy {
  readonly version: string
  readonly vectorTopK: number
  readonly lexicalTopK: number
  readonly graphTopK: number
  readonly recentWindowTopK: number
  readonly validatedReferenceTopK: number
  readonly requireQmd: boolean
  readonly requireLexical: boolean
  readonly failMode: "fail_closed" | "fail_open_with_flag"
  readonly tieBreakOrder: ReadonlyArray<
    | "score_desc"
    | "published_at_desc"
    | "canonical_item_id_asc"
  >
}
```

The initial implementation should choose explicit defaults rather than leaving these to implementer judgment.

### Evidence bundle composition

The evidence bundle should include:

- nearest canonical items by vector similarity
- exact lexical matches from regex and keyword search
- recent items in the same topic window
- connected graph neighbors
- prior claims or updates on the same entity, artifact, or idea
- trusted references from validated nodes where available

### Evidence bundle contract

```ts
export interface EvidenceBundle {
  readonly candidateItemId: string
  readonly retrievalPolicyVersion: string
  readonly vectorMatches: ReadonlyArray<EvidenceItemRef>
  readonly lexicalMatches: ReadonlyArray<EvidenceItemRef>
  readonly graphMatches: ReadonlyArray<EvidenceItemRef>
  readonly recentWindowMatches: ReadonlyArray<EvidenceItemRef>
  readonly validatedReferences: ReadonlyArray<EvidenceItemRef>
}

export interface EvidenceItemRef {
  readonly canonicalItemId: string
  readonly reason:
    | "vector_neighbor"
    | "lexical_match"
    | "graph_neighbor"
    | "recent_window"
    | "validated_reference"
  readonly score: number | null
  readonly metadata: Record<string, unknown>
}
```

### Structured-output rule for LLMs

Whenever an LLM call is expected to produce data that the system will parse, persist, score, or route on, prefer structured outputs tied to an explicit schema.

This applies to:

- query expansion
- semantic dedupe judgments
- novelty judgments
- idea linking
- idea-frequency labeling
- content-state labeling
- review queue scoring
- eval scoring
- research-loop outcome summaries

Freeform text is acceptable for human-facing summaries and explanatory rationale, but not for primary machine-consumed records when structured-output support exists.

## Domain types and interfaces

### Core enumerations

```ts
export type TopicId =
  | "ai"
  | "context-engineering"
  | "agentic-engineering"

export type QueryClass =
  | "seeded"
  | "expanded"
  | "pinned"

export type AcquisitionStrategy =
  | "search"
  | "feed"
  | "curated"
  | "monitor"
  | "pretraining_mining"

export type ContentMode =
  | "news"
  | "evergreen"
  | "validated_reference"
  | "unclear"

export type ReviewValue =
  | "review_now"
  | "review_if_time"
  | "archive_only"

export type UserUtility =
  | "actionable"
  | "contextual"
  | "interesting_but_low_value"
  | "not_useful"

export type ValidationStatus =
  | "unverified"
  | "partially_validated"
  | "validated"
  | "contradicted"

export type NoveltyLabel =
  | "novel_topic"
  | "novel_angle"
  | "material_update"
  | "known_topic_low_novelty"
  | "duplicate"

export type IdeaFrequencyLabel =
  | "emerging"
  | "recurring"
  | "spiking"
  | "persistent_background"
  | "fading"
```

### Query and acquisition contracts

```ts
export interface QueryIntent {
  readonly id: string
  readonly topicId: TopicId
  readonly queryClass: QueryClass
  readonly queryText: string
  readonly strategy: AcquisitionStrategy
  readonly sourceId: string | null
  readonly priority: number
  readonly tags: ReadonlyArray<string>
  readonly excludeFromQueryEval: boolean
  readonly metadata: Record<string, unknown>
}

export interface QueryExecution {
  readonly id: string
  readonly runId: string
  readonly queryIntentId: string
  readonly providerId: string | null
  readonly strategy: AcquisitionStrategy
  readonly costUsd: number | null
  readonly rawHitCount: number
  readonly canonicalHitCount: number
  readonly noveltyYield: number
  readonly frequencyYield: number
  readonly metadata: Record<string, unknown>
}

export interface CandidateItem {
  readonly id: string
  readonly runId: string
  readonly acquisitionStrategy: AcquisitionStrategy
  readonly acquisitionAdapterId: string
  readonly providerId: string | null
  readonly sourceType: string
  readonly sourceId: string | null
  readonly canonicalUrl: string | null
  readonly title: string | null
  readonly summary: string | null
  readonly publishedAt: string | null
  readonly rawPayload: Record<string, unknown>
  readonly metadata: Record<string, unknown>
}
```

## Query policy

Query classes:

- `seeded`
- `expanded`
- `pinned`

Pinned queries:

- always run if enabled
- consume reserved budget
- are excluded from query-planning evals
- still contribute candidates to novelty and recurrence analysis

Track query performance over time:

- executions
- cost
- raw hits
- canonical hits
- novelty yield
- recurrence yield
- digest contribution
- golden contribution
- duplicate rate

## Strategy and provider scoring

Track strategy performance over time:

- candidate yield
- accepted novelty yield
- accepted frequency yield
- validated-reference yield
- duplicate rate
- cost per accepted signal
- latency to useful signal
- golden contribution rate
- digest contribution rate

Track provider performance over time:

- accepted novel items
- accepted recurring-idea contributions
- material update contributions
- duplicate rate
- cost per accepted signal
- golden contribution
- latency

Track feature usefulness over time where possible:

- feature coverage rate
- feature sparsity
- feature stability by version
- marginal contribution to eval scores
- marginal contribution to downstream reward estimates

This telemetry becomes the input for later bandit and RL policies.

# Open Questions
- [ ] Which retrieval-policy defaults should be hard-coded first for each topic window and budget class?
- [ ] Should `pretraining_mining` emit its hypotheses into the same operator-facing queue as other strategies, or should it land in a separate review lane before normal query execution?
