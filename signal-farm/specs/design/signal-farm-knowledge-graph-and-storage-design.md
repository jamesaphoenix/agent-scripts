---
kind: spec
spec_type: design
doc_id: doc-7c1eb9b6ef70
name: signal-farm-knowledge-graph-and-storage-design
title: "Signal Farm Knowledge Graph and Storage Design"
status: draft
version: 1
owners:
  - jamesaphoenix
summary: "Technical design for the Postgres-backed canonical item, property graph, and feature storage model of signal-farm."
domain: signal-farm
tags:
  - design
  - signal-farm
  - knowledge
  - graph
  - storage
depends_on: []
supersedes: []
implements: signal-farm-knowledge-graph-and-storage-prd
last_reviewed_at: 2026-03-28
---

# Plan

> Working plan: [/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md](/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md)

This design doc is the knowledge-graph-and-storage slice of the signal-farm decomposition. It preserves the canonical item, feature, and storage source text verbatim while defining the subsystem boundary future agents should implement.

# Summary

Knowledge graph and storage owns the durable representation of what `signal-farm` knows: canonical items, embeddings, graph nodes and edges, idea links, feature records, and the identity rules that keep those structures stable over time. This subsystem is the storage backbone for acquisition retrieval, novelty labeling, recurrence scoring, replay, and policy learning.

# Architecture
## System Context

This subsystem sits beneath acquisition, labeling, review, replay, and research. It depends on the platform-core repository boundary rules and exposes the canonical storage surface that evidence retrieval and downstream scoring call into.

## Components

- Canonical item storage and embedding retrieval.
- Property-graph node and edge storage.
- Idea and idea-link state that supports recurrence analysis.
- Feature record storage, namespaces, and aggregates.
- Deterministic identity and merge logic for graph families.

## Flow of Control

Normalized candidates become canonical items, canonical items attach to graph nodes and edges, and features are appended as new derived signals are extracted. Retrieval-facing services then read this state through snapshot-aware repository queries rather than through mutable in-memory graphs.

# Interfaces
Use this section for implementation seams and boundary contracts. Add the subsections that fit the subsystem; do not collapse concrete design into one generic YAML list.

## Types

This subsystem owns `CanonicalItem` and `FeatureRecord`, plus the storage-facing semantics of graph node families, edge families, natural keys, and edge metadata. It also carries the temporal fields that let downstream systems filter state at a given knowledge boundary.

## Context

Storage layers compose Postgres repositories, `pgvector` retrieval, and graph-query support behind repository interfaces. Context should include snapshot boundaries, provenance requirements, and feature namespace versions so reads and writes stay time-aware.

## Repositories

The key repositories are `CanonicalItemRepository`, `GraphRepository`, and `FeatureRepository`, with supporting tables for embeddings, ideas, idea links, labels, and research telemetry. Repositories own transactional writes, merge safety, and searchable retrieval plans.

## Services

Storage-facing services are thin wrappers around repository operations: canonical upsert, graph upsert, neighborhood query, feature attachment, and snapshot-filtered retrieval. Domain services above this layer may decide what to write, but they must not bypass these persistence seams.

## Workflows and Jobs

Embedding generation, graph upsert, and feature extraction are the recurring jobs most likely to touch this subsystem. Each of those flows must append new knowledge or superseding versions rather than mutating historical rows in place.

## External Providers and Adapters

This subsystem has no direct provider boundary of its own. Its external dependency is Postgres with `pgvector`, and everything else reaches it through repository contracts.

## Events

Canonical-item creation, graph-edge upsert, idea-link updates, feature insertion, and label creation are its durable event-like outputs. Ordering matters when superseding old rows, because snapshot visibility depends on `knowledgeAvailableAt` and `supersededAt`.

## Endpoints (optional)

Any exposed inspection endpoints should focus on canonical item lookup, graph neighborhoods, edge metadata, and feature inspection. They should not provide direct mutating storage access outside the service and repository boundaries.

## Machine-readable Contract Appendix (optional)

The source text below carries the authoritative contract excerpts for canonical items and features. Generated schemas should be derived from those definitions plus the table and graph model captured in the storage section.

# Data Model

This subsystem owns the authoritative persistent representation of canonical items, embeddings, graph nodes, graph edges, ideas, idea links, labels, feature records, and policy telemetry. It also owns the identity and supersession fields required to make that state replay-safe.

## Database Schema

The authoritative table groups are `canonical_items`, `canonical_item_embeddings`, `graph_nodes`, `graph_edges`, `ideas`, `idea_links`, `idea_frequency_windows`, `feature_records`, `feature_namespaces`, and `feature_aggregates`, with related label and policy tables referenced by downstream docs. `pgvector` is the required embedding layer.

## Stored State Shapes

Canonical items store normalized source identity and downstream review-oriented fields, while graph rows store node family, edge family, provenance, time fields, and version keys. Feature records store target type, namespace, key, value, source, feature version, and temporal visibility.

## Derived and Read Models

Derived reads include graph neighborhoods, vector-neighbor retrieval, feature aggregates, and queries over items surfaced by provider or topic over time. These are projections over append-only base rows rather than mutable caches treated as truth.

## Indexes and Constraints

Material constraints include natural-key uniqueness for node families, `(edge_type, from_node_id, to_node_id, version_key)` uniqueness for edges, embedding indexes for vector search, and temporal indexes over `knowledgeAvailableAt` and `supersededAt`. Feature namespaces and versions also need keyed lookup paths to support append-only growth.

## State Transitions and Lifecycles

Storage rows do not have workflow status machines, but they do have lifecycle semantics: first seen, last seen, superseded, and knowledge availability. Corrections and re-judgments must create new rows or superseding versions instead of mutating the original record.

# Invariants
```yaml
invariants:
  - id: INV-SFKG-001
    statement: Postgres is the authoritative store for canonical items, graph state, features, labels, and policy telemetry.
    source_requirements:
      - REQ-SFKG-001
  - id: INV-SFKG-002
    statement: The KB/KG is represented as a property graph with deterministic node and edge identities.
    source_requirements:
      - REQ-SFKG-002
      - REQ-SFKG-003
      - REQ-SFKG-004
  - id: INV-SFKG-003
    statement: Edge rows preserve searchable provenance and temporal metadata.
    source_requirements:
      - REQ-SFKG-005
  - id: INV-SFKG-004
    statement: Experimental features are appended through namespaced feature records rather than one-off schema changes.
    source_requirements:
      - REQ-SFKG-006
  - id: INV-SFKG-005
    statement: Canonical items, graph records, labels, and feature rows remain snapshot-addressable over time.
    source_requirements:
      - REQ-SFKG-007
```

# Failure Modes
```yaml
failure_modes:
  - id: FM-SFKG-001
    condition: Graph natural keys or edge uniqueness rules are missing or inconsistent.
    impact: Duplicate or conflicting graph state corrupts retrieval and scoring.
    mitigation: Enforce explicit natural keys and uniqueness constraints at the database layer.
  - id: FM-SFKG-002
    condition: `same_as` merges silently collapse conflicting validated nodes.
    impact: Provenance and contradiction history are lost.
    mitigation: Preserve prior provenance and reject unsafe validated merges without explicit evidence.
  - id: FM-SFKG-003
    condition: New experiments add bespoke storage columns instead of feature records.
    impact: Schema drift makes historical comparisons and ML feature extraction brittle.
    mitigation: Route new experimental signals through append-only, namespaced feature storage.
  - id: FM-SFKG-004
    condition: Rows are updated in place rather than superseded with temporal visibility fields.
    impact: Snapshot filtering and replay become nondeterministic.
    mitigation: Use append-only writes plus `knowledgeAvailableAt` and `supersededAt` semantics.
```

# Verification
```yaml
verification:
  - id: VER-SFKG-001
    requirement_ids:
      - REQ-SFKG-001
      - REQ-SFKG-002
    invariant_ids:
      - INV-SFKG-001
    test: packages/postgres/test/storage/canonical-schema.integration.test.ts
    assertion: Canonical items, embeddings, graph rows, and feature rows persist in Postgres-backed authoritative tables.
  - id: VER-SFKG-002
    requirement_ids:
      - REQ-SFKG-003
      - REQ-SFKG-004
    invariant_ids:
      - INV-SFKG-002
    test: packages/postgres/test/storage/graph-identity.integration.test.ts
    assertion: Natural-key upserts preserve provenance and edge uniqueness while rejecting unsafe silent merges.
  - id: VER-SFKG-003
    requirement_ids:
      - REQ-SFKG-005
    invariant_ids:
      - INV-SFKG-003
    test: packages/postgres/test/storage/edge-metadata.integration.test.ts
    assertion: Edge metadata remains queryable by provenance, temporal, provider, and model fields.
  - id: VER-SFKG-004
    requirement_ids:
      - REQ-SFKG-006
    invariant_ids:
      - INV-SFKG-004
    test: packages/postgres/test/storage/feature-records.integration.test.ts
    assertion: New experimental signals append through namespaced feature records without schema rewrites.
  - id: VER-SFKG-005
    requirement_ids:
      - REQ-SFKG-007
    invariant_ids:
      - INV-SFKG-005
    test: packages/postgres/test/storage/snapshot-visibility.integration.test.ts
    assertion: Snapshot reads include only rows visible at the requested `knowledgeAvailableAt` boundary.
```

# Testing Strategy
## Traceability Matrix

- `REQ-SFKG-001`, `REQ-SFKG-002`, `INV-SFKG-001` -> `packages/postgres/test/storage/canonical-schema.integration.test.ts`
- `REQ-SFKG-003`, `REQ-SFKG-004`, `INV-SFKG-002` -> `packages/postgres/test/storage/graph-identity.integration.test.ts`
- `REQ-SFKG-005`, `INV-SFKG-003` -> `packages/postgres/test/storage/edge-metadata.integration.test.ts`
- `REQ-SFKG-006`, `INV-SFKG-004` -> `packages/postgres/test/storage/feature-records.integration.test.ts`
- `REQ-SFKG-007`, `INV-SFKG-005` -> `packages/postgres/test/storage/snapshot-visibility.integration.test.ts`

## Integration Scenarios

- Upsert canonical items from repeated normalized candidates and assert deterministic identity resolution.
- Insert graph nodes and edges with the same natural keys twice and assert provenance is preserved without duplicate semantic state.
- Query graph edges by confidence, provider, prompt version, and novelty window and assert searchable metadata is available.
- Append multiple versions of the same experimental feature and assert old versions remain addressable.
- Execute snapshot reads across superseded and unsuperseded records and assert visibility follows `knowledgeAvailableAt` and `supersededAt`.

## Test Files

- `packages/postgres/test/storage/canonical-schema.integration.test.ts`
- `packages/postgres/test/storage/edge-metadata.integration.test.ts`
- `packages/postgres/test/storage/feature-records.integration.test.ts`
- `packages/postgres/test/storage/graph-identity.integration.test.ts`
- `packages/postgres/test/storage/snapshot-visibility.integration.test.ts`
- `packages/postgres/test/storage/vector-retrieval.integration.test.ts`

# Source Text

The following source sections are copied verbatim from `specs/signal-farm.md` for subsystem fidelity during implementation.

## Domain types and interfaces

### Canonical item and labeling contracts

```ts
export interface CanonicalItem {
  readonly id: string
  readonly sourceId: string
  readonly itemType: string
  readonly canonicalUrl: string | null
  readonly title: string
  readonly summary: string | null
  readonly publishedAt: string | null
  readonly contentMode: ContentMode | null
  readonly reviewValue: ReviewValue | null
  readonly userUtility: UserUtility | null
  readonly validationStatus: ValidationStatus | null
  readonly perishabilityScore: number | null
  readonly sourceAuthorityScore: number | null
  readonly metadata: Record<string, unknown>
}

export interface NoveltyJudgment {
  readonly canonicalItemId: string
  readonly label: NoveltyLabel
  readonly confidence: number
  readonly modelId: string
  readonly promptVersion: string
  readonly retrievalPolicyVersion: string
  readonly rationale: string
  readonly evidenceItemIds: ReadonlyArray<string>
  readonly createdAt: string
  readonly knowledgeAvailableAt: string
}

export interface ContentStateJudgment {
  readonly canonicalItemId: string
  readonly contentMode: ContentMode
  readonly reviewValue: ReviewValue
  readonly userUtility: UserUtility
  readonly validationStatus: ValidationStatus
  readonly perishabilityScore: number | null
  readonly sourceAuthorityScore: number | null
  readonly rationale: string
  readonly createdAt: string
  readonly knowledgeAvailableAt: string
}
```

### Feature contracts

The system should support append-only, namespaced features on every important entity so new model inputs can be added without destructive schema changes.

```ts
export type FeatureTargetType =
  | "canonical_item"
  | "idea"
  | "graph_edge"
  | "query_execution"
  | "provider_call"
  | "review_queue_item"
  | "policy_example"

export interface FeatureRecord {
  readonly id: string
  readonly targetType: FeatureTargetType
  readonly targetId: string
  readonly namespace: string
  readonly featureKey: string
  readonly featureValue:
    | string
    | number
    | boolean
    | ReadonlyArray<string>
    | ReadonlyArray<number>
    | Record<string, unknown>
    | null
  readonly featureType:
    | "scalar"
    | "categorical"
    | "vector"
    | "json"
    | "text"
  readonly featureVersion: string
  readonly source:
    | "ingestion"
    | "normalization"
    | "graph"
    | "llm_labeler"
    | "policy"
    | "human_review"
    | "derived_metric"
  readonly createdAt: string
  readonly knowledgeAvailableAt: string
  readonly supersededAt: string | null
}
```

Where feature extraction is LLM-assisted and the output is machine-consumed, the extraction call should use structured outputs against an explicit `FeatureRecord`-compatible schema or a narrower task-specific schema.

## Storage and schema

Postgres is the system of record for:

- runs
- provider calls
- cassettes and replay
- eval caches
- query execution and scoring
- canonical items
- graph nodes and edges
- idea nodes and windows
- novelty and content-state labels
- review queue items
- provisional and confirmed goldens
- policy telemetry

`pgvector` is used for embedding retrieval.

### Required table groups

#### Operational run state

- `runs`
- `run_events`
- `provider_calls`
- `provider_call_costs`

#### Replay and eval state

- `cassette_records`
- `cassette_payloads`
- `cassette_replays`
- `eval_cache_entries`
- `eval_cases`
- `eval_scores`

#### Query and strategy state

- `query_intents`
- `query_executions`
- `query_performance_windows`
- `strategy_performance_windows`
- `provider_performance_windows`

#### Canonical and graph state

- `canonical_items`
- `canonical_item_embeddings`
- `graph_nodes`
- `graph_edges`
- `ideas`
- `idea_links`
- `idea_frequency_windows`

#### Label and queue state

- `novelty_labels`
- `content_state_labels`
- `review_queue_items`

#### Feature state

- `feature_records`
- `feature_namespaces`
- `feature_aggregates`

#### Golden and policy state

- `provisional_goldens`
- `confirmed_goldens`
- `policy_feature_rows`
- `policy_reward_rows`
- `research_loop_definitions`
- `research_iterations`
- `research_outcomes`

### Property graph model

The KB/KG is a property graph implemented in Postgres.

Node families:

- `item`
- `entity`
- `topic`
- `event`
- `artifact`
- `claim`
- `idea`
- `query`
- `provider`
- `customer_profile`

Edge families:

- `mentions`
- `about`
- `same_as`
- `updates`
- `supports`
- `contradicts`
- `derived_from_query`
- `surfaced_by_provider`
- `belongs_to_topic`
- `instantiates_idea`
- `relevant_for_customer`

### Identity and merge rules

The graph must define deterministic identity and merge behavior.

Required natural keys:

- `item`
  - canonical source id, else canonical URL hash, else stable fingerprint
- `entity`
  - namespace + normalized external id or normalized canonical name
- `topic`
  - stable topic id
- `event`
  - event key derived from normalized subject + time window + event type
- `artifact`
  - stable artifact id such as repo slug, paper id, doc path, pricing page id
- `claim`
  - claim hash over normalized assertion + subject + scope
- `idea`
  - topic id + normalized idea slug
- `query`
  - normalized query intent hash
- `provider`
  - provider id
- `customer_profile`
  - customer profile id

Required merge rules:

- upserts must preserve prior provenance
- `same_as` must not silently merge conflicting validated nodes without provenance
- `updates` edges must remain directional and time-aware
- `instantiates_idea` must be versionable so idea-linking changes can be compared over time

Required edge uniqueness:

- one edge row per `(edge_type, from_node_id, to_node_id, version_key)` unless explicitly multi-valued

### Edge metadata requirements

Edges must support searchable metadata. At minimum:

- confidence
- provenance source ids
- first_seen_at
- last_seen_at
- novelty window
- frequency window
- provider id
- topic tags
- customer tags
- model id
- prompt config version
- created_at
- knowledge_available_at
- superseded_at where the edge model supports replacement

### Feature extensibility rules

Feature capture must be append-only and namespaced.

That means:

- do not add one-off top-level columns for every new experiment feature
- add new features through `feature_records` unless the field is a stable first-class domain attribute
- allow multiple versions of the same feature key over time
- keep source provenance for all derived features
- permit feature extraction at item, idea, graph-edge, query, provider-call, and policy-example levels

This is required so that future ML and RL training pipelines can build feature matrices without repeated schema surgery.

The database must support queries like:

- “items surfaced by provider X in the last 7 days”
- “ideas linked to topic Y via update edges with confidence > 0.8”
- “claims contradicting existing validated claims on the same artifact”

# Open Questions
- [ ] Should canonical item identity prefer source-scoped ids over canonical URL hashes even when both are present but disagree?
- [ ] Do `feature_aggregates` belong in the first implementation, or should they wait until the first downstream policy learner needs them?
