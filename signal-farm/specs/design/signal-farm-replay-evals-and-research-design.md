---
kind: spec
spec_type: design
doc_id: doc-7cd071ab7b56
name: signal-farm-replay-evals-and-research-design
title: "Signal Farm Replay, Evals, and Research Design"
status: draft
version: 1
owners:
  - docs-team
summary: "Technical design for Signal Farm Replay, Evals, and Research Design."
domain: signal-farm-replay-evals-and-research
tags:
  - design
  - signal
  - farm
  - replay
  - evals
  - and
  - research
depends_on: []
supersedes: []
implements: null
last_reviewed_at: 2026-03-28
---

# Summary
Describe the core technical approach and boundary decisions.

# Architecture
## System Context
Describe where this design sits in the wider system and what it depends on.

## Components
List the major services, adapters, workflows, and boundaries.

## Flow of Control
Describe the main runtime flow, state transitions, and ownership handoffs.

# Interfaces
Use this section for implementation seams and boundary contracts. Add the subsections that fit the subsystem; do not collapse concrete design into one generic YAML list.

## Types
Document the important request, response, domain, and event shapes. Include real Effect Schema or TypeScript definitions when they matter.

## Context
Describe the runtime context, dependency injection, module boundaries, and how layers are composed.

## Repositories
Document repository contracts, domain ports, persistence seams, and the responsibilities of each abstraction.

## Services
Describe the application services, orchestration services, or internal APIs that own the main behaviors.

## Workflows and Jobs
Describe Temporal workflows, queues, cron jobs, compensators, or batch processors when they are part of the design.

## External Providers and Adapters
Describe third-party APIs, provider adapters, rate-limit boundaries, and integration-specific contracts.

## Events
Document produced and consumed events, payload shapes, ordering assumptions, and idempotency rules when events are relevant.

## Endpoints (optional)
Add this subsection only when the design exposes HTTP or REST routes. Keep route inventory here instead of treating endpoints as the whole Interfaces section.

## Machine-readable Contract Appendix (optional)
If useful, include a fenced yaml interfaces: block here for machine-readable runtime contracts. It is optional and should supplement, not replace, the concrete subsections above.

# Data Model
Document actual authoritative state, not just entity names.

## Database Schema
Include concrete DDL, table definitions, columns, relationships, and ownership rules when persistence changes or when schema detail is central to the design.

## Stored State Shapes
Describe persisted documents, rows, or durable state objects and the meaning of key fields.

## Derived and Read Models
Document projections, materialized views, caches, denormalized read models, and derived aggregates.

## Indexes and Constraints
Document the indexes, uniqueness rules, foreign keys, check constraints, and partitioning choices that materially affect behavior.

## State Transitions and Lifecycles
Document status machines, retention phases, archival behavior, or lifecycle transitions when they matter to correctness.

# Invariants
```yaml
invariants: []
```

# Failure Modes
```yaml
failure_modes: []
```

# Verification
```yaml
verification: []
```

# Testing Strategy
## Traceability Matrix
Map every `REQ-*` and `INV-*` to exact test files and concrete assertions.

## Integration Scenarios
List concrete setup/action/assertion scenarios, including happy path, validation, auth, dependency failure, retries/idempotency, concurrency, and data integrity where relevant.

## Test Files
Name the exact test files to add or update.

# Open Questions
- [ ] Unresolved design decisions
