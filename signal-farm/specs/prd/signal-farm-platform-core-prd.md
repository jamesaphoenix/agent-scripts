---
kind: spec
spec_type: prd
doc_id: doc-277eba9099f5
name: signal-farm-platform-core-prd
title: "Signal Farm Platform Core PRD"
status: draft
version: 1
owners:
  - jamesaphoenix
summary: "Requirements for the service-backed platform core, orchestration boundaries, and shared runtime surfaces of signal-farm."
domain: signal-farm
tags:
  - prd
  - signal-farm
  - platform-core
  - architecture
  - orchestration
depends_on: []
supersedes: []
implements: null
last_reviewed_at: 2026-03-28
plan: /Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md
---

# Plan

> Working plan: [/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md](/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md)

This PRD captures the platform-core slice of the signal-farm decomposition plan. It defines the runtime boundaries, orchestration rules, and shared contract requirements that the other subsystem docs depend on.

# Summary

This PRD defines the service-backed platform core for `signal-farm`: the runtime boundaries, shared contracts, lifecycle state, and thin API/CLI surfaces that every other subsystem builds on. It exists so acquisition, storage, labeling, replay, and research behavior all execute through one consistent orchestration model.

# Problem

Without a dedicated platform-core contract, each surface or subsystem can drift into its own workflow logic, storage calls, or ad hoc LLM parsing behavior. `signal-farm` needs one root subsystem that fixes the service architecture, idempotent lifecycle rules, and contract boundaries before narrower subsystems are implemented by agents.

# Scope

Included: service-backed runtime shape, shared contracts, orchestration flow, run/provider/research lifecycles, thin operator surfaces, and the acceptance/default rules that constrain the whole system.
Excluded: acquisition-specific retrieval policies, graph schema details, novelty and review scoring behavior, and replay/eval policy logic owned by companion subsystem docs.

# Requirements
```yaml
ears_requirements:
  - id: REQ-SFPC-001
    kind: ubiquitous
    statement: the system shall expose materially useful operations behind service interfaces that can be called by CLI, API, and future worker surfaces.
    priority: must
    rationale: Business logic has to live once in the core rather than separately in each surface.
  - id: REQ-SFPC-002
    kind: ubiquitous
    statement: the system shall keep API and CLI surfaces thin over the same contract-driven core.
    priority: must
    rationale: Operator entrypoints must not fork behavior from the internal service boundary.
  - id: REQ-SFPC-003
    kind: ubiquitous
    statement: the system shall isolate Postgres access behind repository interfaces.
    priority: must
    rationale: Storage concerns need to remain swappable, mockable, and outside orchestration code.
  - id: REQ-SFPC-004
    kind: ubiquitous
    statement: the system shall define explicit contracts for CLI, API, config, provider, canonical-item, replay, eval, and review payloads.
    priority: must
    rationale: Silent JSON drift would break replayability and cross-surface compatibility.
  - id: REQ-SFPC-005
    kind: event-driven
    when: a run, provider call, or research iteration changes lifecycle state
    statement: the system shall enforce explicit state-machine transitions and durable idempotent writes.
    priority: must
    rationale: Replayable and retried operations need deterministic lifecycle behavior.
  - id: REQ-SFPC-006
    kind: ubiquitous
    statement: the system shall use structured-output or schema-constrained modes for machine-consumed LLM outputs whenever the provider supports them.
    priority: must
    rationale: Core orchestration should not depend on brittle freeform parsing.
  - id: REQ-SFPC-007
    kind: ubiquitous
    statement: the system shall keep facts, edges, labels, and feature records append-only and snapshot-addressable.
    priority: must
    rationale: Replay, eval, and temporal comparisons depend on immutable historical state.
  - id: REQ-SFPC-008
    kind: ubiquitous
    statement: the system shall preserve a single end-to-end candidate pipeline from acquisition planning through telemetry persistence.
    priority: must
    rationale: The platform core must define the canonical orchestration path before subsystem specialization.
```

# Acceptance Criteria
```yaml
acceptance_criteria:
  - id: AC-SFPC-001
    statement: CLI and API implementations call shared services instead of embedding novelty, queueing, or SQL logic inline.
  - id: AC-SFPC-002
    statement: Run, provider-call, and research-iteration lifecycle transitions are explicit and reject invalid duplicate transitions under retry.
  - id: AC-SFPC-003
    statement: Contract-owned schemas exist for the core payload families and are shared across subsystem boundaries.
  - id: AC-SFPC-004
    statement: Repository interfaces isolate Postgres access from orchestration services.
  - id: AC-SFPC-005
    statement: The orchestrated pipeline is traceable from acquisition planning through digest generation, eval sampling, and telemetry persistence.
```

# Non-goals
- Defining acquisition-specific retrieval rules or fairness policies.
- Defining property-graph node and edge semantics in detail.
- Defining novelty, recurrence, or review scoring rubrics beyond the platform requirements they depend on.
