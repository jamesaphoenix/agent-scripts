---
kind: spec
spec_type: prd
doc_id: doc-dfc4ff5536d2
name: signal-farm-acquisition-and-evidence-prd
title: "Signal Farm Acquisition and Evidence PRD"
status: draft
version: 1
owners:
  - jamesaphoenix
summary: "Requirements for signal acquisition, fairness, retrieval policy, and evidence construction in signal-farm."
domain: signal-farm
tags:
  - prd
  - signal-farm
  - acquisition
  - evidence
  - retrieval
depends_on: []
supersedes: []
implements: null
last_reviewed_at: 2026-03-28
plan: /Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md
---

# Plan

> Working plan: [/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md](/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md)

This PRD captures the acquisition-and-evidence slice of the signal-farm decomposition. It defines how the system acquires candidate signals, binds them to a stable knowledge baseline, and assembles deterministic evidence bundles before any novelty or recurrence judgment.

# Summary

This PRD defines the candidate-acquisition and evidence-building subsystem for `signal-farm`. It covers strategy families, provenance, fairness, snapshot binding, retrieval policies, and the scoring telemetry that later policies will learn from.

# Problem

If acquisition, snapshot binding, and evidence retrieval are underspecified, novelty labeling becomes non-comparable across providers and runs. `signal-farm` needs a dedicated subsystem that guarantees every candidate reaches downstream judgment through the same provenance-preserving and snapshot-safe evidence path.

# Scope

Included: acquisition strategy families, query planning inputs, candidate provenance, fairness model, knowledge snapshots, evidence bundles, query policy, and query/strategy/provider performance telemetry.
Excluded: canonical graph storage internals, novelty and review labeling semantics beyond their evidence prerequisites, and replay or research-loop policy promotion logic.

# Requirements
```yaml
ears_requirements:
  - id: REQ-SFAE-001
    kind: ubiquitous
    statement: the system shall support `search`, `feed`, `curated`, `monitor`, and `pretraining_mining` acquisition strategies as first-class strategy families.
    priority: must
    rationale: Signal-farm is designed around multiple acquisition lanes rather than one search loop.
  - id: REQ-SFAE-002
    kind: ubiquitous
    statement: the system shall normalize every acquisition strategy into the same `CandidateItem` contract with acquisition provenance preserved.
    priority: must
    rationale: Downstream dedupe and judgment require a uniform candidate model.
  - id: REQ-SFAE-003
    kind: ubiquitous
    statement: the system shall judge providers and strategies against a shared KB/KG evidence baseline rather than against each other.
    priority: must
    rationale: Fairness depends on a single shared comparison baseline.
  - id: REQ-SFAE-004
    kind: event-driven
    when: a run or evaluation begins
    statement: the system shall bind acquisition and evidence retrieval to an explicit knowledge snapshot reference and retrieval policy version.
    priority: must
    rationale: Temporal leakage breaks comparability across runs and evals.
  - id: REQ-SFAE-005
    kind: ubiquitous
    statement: the system shall build bounded evidence bundles using QMD semantic search, 3-5 lexical searches in parallel, graph traversal, vector neighbors, and recent-window filtering.
    priority: must
    rationale: Novelty and recurrence judgments depend on a consistent evidence set.
  - id: REQ-SFAE-006
    kind: ubiquitous
    statement: the system shall define a first-class retrieval policy with explicit limits, merge rules, tie-breaks, duplicate collapse rules, and fail behavior.
    priority: must
    rationale: Two implementations need materially comparable evidence bundles for the same inputs.
  - id: REQ-SFAE-007
    kind: state-driven
    while: pinned queries are enabled
    statement: the system shall run them with reserved budget while excluding them from query-planning evals.
    priority: must
    rationale: Pinned operational inputs should not distort planning-quality comparisons.
  - id: REQ-SFAE-008
    kind: ubiquitous
    statement: the system shall track query, strategy, and provider performance telemetry over time.
    priority: must
    rationale: Later bandit and RL policies need durable performance history.
  - id: REQ-SFAE-009
    kind: ubiquitous
    statement: the system shall use structured outputs for machine-consumed acquisition and evidence judgments whenever the provider supports them.
    priority: must
    rationale: Query expansion, dedupe, novelty, and queue-routing outputs need schema-safe parsing.
```

# Acceptance Criteria
```yaml
acceptance_criteria:
  - id: AC-SFAE-001
    statement: Every candidate record preserves acquisition strategy, adapter, source, timestamp, and cost provenance.
  - id: AC-SFAE-002
    statement: Two runs with the same snapshot and retrieval policy produce materially comparable evidence bundles for the same candidate item.
  - id: AC-SFAE-003
    statement: Evidence retrieval always combines semantic and lexical search before novelty or recurrence judgment.
  - id: AC-SFAE-004
    statement: Pinned queries remain operationally enabled without polluting query-planning eval scores.
  - id: AC-SFAE-005
    statement: Query, strategy, and provider performance windows persist cost, yield, and duplicate-rate telemetry.
```

# Non-goals
- Comparing providers directly to one another for novelty.
- Allowing hypothesis-only acquisition to create canonical truth without grounding through standard channels.
- Letting whole-corpus comparison replace bounded evidence bundles.
