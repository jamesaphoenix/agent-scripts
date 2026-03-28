---
kind: spec
spec_type: prd
doc_id: doc-d211e38140d9
name: signal-farm-knowledge-graph-and-storage-prd
title: "Signal Farm Knowledge Graph and Storage PRD"
status: draft
version: 1
owners:
  - jamesaphoenix
summary: "Requirements for the Postgres-backed canonical item, property graph, and feature storage model of signal-farm."
domain: signal-farm
tags:
  - prd
  - signal-farm
  - knowledge
  - graph
  - storage
depends_on: []
supersedes: []
implements: null
last_reviewed_at: 2026-03-28
plan: /Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md
---

# Plan

> Working plan: [/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md](/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md)

This PRD captures the knowledge-graph-and-storage slice of the signal-farm decomposition. It defines the durable data model that canonical items, graph structures, features, and downstream policy learning depend on.

# Summary

This PRD defines the Postgres-backed canonical storage subsystem for `signal-farm`. It covers canonical items, the property graph, deterministic identity and merge behavior, feature storage, and the storage rules required for snapshot-safe retrieval and future ML or RL training.

# Problem

`signal-farm` cannot judge novelty, recurrence, or policy quality unless its knowledge base and graph state are durable, queryable, and temporally safe. The project needs one subsystem that fixes the canonical storage model and prevents schema drift, silent merges, or feature-column sprawl as the system evolves.

# Scope

Included: Postgres as system of record, canonical item storage, graph nodes and edges, embeddings, feature records, deterministic identity rules, merge rules, and searchable edge metadata.
Excluded: acquisition fairness policy, review routing policy, replay promotion policy, and operator-surface orchestration rules already owned by companion docs.

# Requirements
```yaml
ears_requirements:
  - id: REQ-SFKG-001
    kind: ubiquitous
    statement: the system shall use Postgres as the system of record for canonical items, graph state, labels, replay state, eval state, and policy telemetry.
    priority: must
    rationale: The system needs one authoritative storage layer for all replayable and queryable state.
  - id: REQ-SFKG-002
    kind: ubiquitous
    statement: the system shall use `pgvector` for embedding retrieval over canonical items.
    priority: must
    rationale: Evidence retrieval depends on embedding-based nearest-neighbor lookup.
  - id: REQ-SFKG-003
    kind: ubiquitous
    statement: the system shall model the KB/KG as a Postgres-backed property graph with the defined node and edge families.
    priority: must
    rationale: Novelty, recurrence, and retrieval all rely on graph-native relationships.
  - id: REQ-SFKG-004
    kind: ubiquitous
    statement: the system shall enforce deterministic natural keys, merge rules, and edge uniqueness constraints for graph data.
    priority: must
    rationale: Silent identity drift or unsafe merges would corrupt the knowledge baseline.
  - id: REQ-SFKG-005
    kind: ubiquitous
    statement: the system shall store searchable edge metadata including provenance, temporal fields, provider identity, and model configuration.
    priority: must
    rationale: Graph edges need enough context to support retrieval, audit, and policy features.
  - id: REQ-SFKG-006
    kind: ubiquitous
    statement: the system shall support append-only, namespaced feature records instead of one-off experimental columns.
    priority: must
    rationale: Future feature growth should not require destructive schema surgery.
  - id: REQ-SFKG-007
    kind: ubiquitous
    statement: the system shall keep canonical items, graph records, labels, and feature records snapshot-addressable.
    priority: must
    rationale: Replay, eval, and temporal retrieval depend on deterministic historical visibility.
```

# Acceptance Criteria
```yaml
acceptance_criteria:
  - id: AC-SFKG-001
    statement: Canonical items, graph nodes, graph edges, ideas, feature records, and embeddings persist in Postgres-backed authoritative tables.
  - id: AC-SFKG-002
    statement: Graph natural keys and merge rules reject unsafe silent merges and preserve provenance.
  - id: AC-SFKG-003
    statement: Edge metadata is queryable by confidence, provenance, temporal fields, provider, topic tags, and prompt or model version.
  - id: AC-SFKG-004
    statement: New experimental features are stored through namespaced feature records rather than new ad hoc columns.
  - id: AC-SFKG-005
    statement: Snapshot filtering can deterministically exclude records not yet available at a given knowledge boundary.
```

# Non-goals
- Letting each experiment add bespoke top-level storage columns.
- Using provider-relative or file-cache-relative storage as the primary source of truth.
- Collapsing graph identity and merge behavior into informal application logic.
