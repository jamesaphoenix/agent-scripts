---
kind: spec
spec_type: design
doc_id: doc-f99413a9ef4b
name: signal-farm-platform-core-design
title: "Signal Farm Platform Core Design"
status: draft
version: 1
owners:
  - jamesaphoenix
summary: "Technical design for the service-backed platform core, orchestration boundaries, and shared runtime surfaces of signal-farm."
domain: signal-farm
tags:
  - design
  - signal-farm
  - platform-core
  - architecture
  - orchestration
depends_on: []
supersedes: []
implements: signal-farm-platform-core-prd
last_reviewed_at: 2026-03-28
---

# Plan

> Working plan: [/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md](/Users/jamesaphoenix/.codex/plans/2026-03-28-signal-farm-spec-decomposition.md)

This design doc is the platform-core slice of the spec decomposition. It owns runtime boundaries, shared orchestration interfaces, lifecycle rules, and operator surfaces, while preserving the corresponding source text from `specs/signal-farm.md` verbatim below.

# Summary

Platform core is the root subsystem that the rest of `signal-farm` hangs off: it fixes the service-backed runtime shape, the shared contract boundaries, the lifecycle state machines, and the thin CLI/API surface rules. The current repository still only has early scaffolding, so this design treats the TypeScript + Effect layout in the source spec as the target implementation shape that future agents should build toward.

# Architecture
## System Context

This subsystem sits above acquisition, storage, labeling, and replay concerns. It owns the process boundaries (`apps/api`, `apps/cli`), the contract and orchestration layers (`packages/contracts`, `packages/core`), and the lifecycle rules that every narrower subsystem must plug into.

## Components

- `apps/api` for the long-lived service boundary.
- `apps/cli` for the thin operator/client surface.
- `packages/contracts` for shared schemas and value objects.
- `packages/core` for orchestration and domain services.
- `packages/postgres` for repository implementations.
- `packages/providers` for provider and acquisition adapters.
- `configs/` and `programs/` for operator-selected policy and loop inputs.

## Flow of Control

The platform core owns the canonical candidate pipeline from acquisition planning through telemetry persistence, and it enforces the run state transitions that bracket each phase. Surface layers gather input, call core services, and hand off to repositories and providers through interfaces rather than direct implementation coupling.

# Interfaces
Use this section for implementation seams and boundary contracts. Add the subsections that fit the subsystem; do not collapse concrete design into one generic YAML list.

## Types

Platform-core-owned types include shared service interfaces, repository interfaces, and lifecycle status enums for runs, provider calls, and research iterations. Schema ownership belongs in `packages/contracts`, not in prompt strings or surface-specific parsing code.

## Context

Effect layers compose repositories, providers, acquisition services, normalization, graph retrieval, novelty, recurrence, review queues, replay, evals, and policy logic. CLI and API layers are clients of that composition root rather than parallel implementations of the same behaviors.

## Repositories

The platform core defines repository seams for runs, queries, canonical items, graph access, replay, evals, features, and research loops. Each repository stays narrow and domain-shaped so orchestration code never reaches directly into SQL.

## Services

The core service layer owns acquisition planning/execution, normalization, dedupe, novelty judgment, idea linking, review assignment, replay, eval scoring, feature attachment, and research loop iteration management. LLM-facing services must accept both policy inputs and explicit output schemas required for structured parsing.

## Workflows and Jobs

The initial workflow is a single end-to-end run pipeline with explicit phases: planning, acquisition, normalization, labeling, review queueing, completion, and failure or abort handling. Research iterations and replay/eval flows are long-running entities that must follow the same idempotent lifecycle discipline.

## External Providers and Adapters

Provider adapters live behind `packages/providers`, remain replay-aware, and may not leak provider-specific parsing into routes or CLI commands. The platform core only depends on provider interfaces and normalized contracts.

## Events

`runs`, `run_events`, `provider_calls`, and research iteration records are the durable event-like entities owned at this layer. Ordering must be explicit enough that retries with the same idempotency key do not duplicate durable records or produce conflicting lifecycle state.

## Endpoints (optional)

The API surface eventually exposes run creation and status, query planning, acquisition execution, novelty and recurrence inspection, review queue access, replay/eval operations, and admin/health endpoints. Route contracts derive from `packages/contracts` and should remain thin wrappers over core services.

## Machine-readable Contract Appendix (optional)

The source text below already carries the authoritative TypeScript interface inventory for this subsystem. Additional machine-readable appendices should be generated from those contract definitions rather than maintained separately by hand.

# Data Model

Platform core owns the authoritative lifecycle state for runs and provider calls, plus the interface contracts that other storage models plug into. It also owns the idempotency rules that protect durable writes during retries and replays.

## Database Schema

The platform-core slice of the schema centers on `runs`, `run_events`, `provider_calls`, and `provider_call_costs`. Companion docs own deeper storage groups, but this subsystem defines the state transitions and service boundaries those tables must support.

## Stored State Shapes

Key durable shapes are run records, run event records, provider call records, and the lifecycle state enumerations that gate orchestration progress. Each durable write that can be retried must be addressable by an idempotency key.

## Derived and Read Models

Platform read models are operator-facing status and inspection views, including run status, query performance inspection, novelty frontier inspection, and cassette statistics. These read paths should remain projections over canonical state rather than separate workflow implementations.

## Indexes and Constraints

The core needs uniqueness around idempotency keys, stable run identifiers, and lifecycle-safe write paths. State transition checks must reject impossible transitions such as skipping directly from `pending` to `completed` without passing through the planned phases.

## State Transitions and Lifecycles

Run, provider-call, and research-iteration states are explicit design elements rather than informal conventions. Every active state must have defined failure and abort exits, and retries with the same key must not create duplicate lifecycle records.

# Invariants
```yaml
invariants:
  - id: INV-SFPC-001
    statement: API and CLI surfaces remain thin clients over shared core services.
    source_requirements:
      - REQ-SFPC-001
      - REQ-SFPC-002
  - id: INV-SFPC-002
    statement: Orchestration services depend on repositories and provider adapters through interfaces rather than direct SQL or parsing logic.
    source_requirements:
      - REQ-SFPC-003
      - REQ-SFPC-004
  - id: INV-SFPC-003
    statement: Run, provider-call, and research-iteration lifecycles are explicit and idempotent under retry.
    source_requirements:
      - REQ-SFPC-005
  - id: INV-SFPC-004
    statement: Machine-consumed LLM outputs use explicit schemas whenever the provider supports structured output.
    source_requirements:
      - REQ-SFPC-006
  - id: INV-SFPC-005
    statement: The platform preserves a single canonical end-to-end pipeline from acquisition planning through telemetry persistence.
    source_requirements:
      - REQ-SFPC-008
```

# Failure Modes
```yaml
failure_modes:
  - id: FM-SFPC-001
    condition: CLI or API handlers embed workflow logic instead of delegating to core services.
    impact: Surface-specific behavior drifts and replay parity breaks.
    mitigation: Keep handlers thin and test for service-call-only orchestration.
  - id: FM-SFPC-002
    condition: Repository boundaries are bypassed by direct SQL in orchestration code.
    impact: Storage coupling makes retries, mocks, and future adapters unsafe.
    mitigation: Enforce repository-only persistence seams and integration tests around those seams.
  - id: FM-SFPC-003
    condition: Lifecycle writes are retried without stable idempotency keys or transition guards.
    impact: Duplicate runs, duplicated provider calls, or inconsistent state progression appear.
    mitigation: Require unique idempotency keys and validate legal transitions at write time.
  - id: FM-SFPC-004
    condition: Structured-output support is ignored for machine-consumed LLM calls.
    impact: Parsing drift corrupts persisted records and downstream routing.
    mitigation: Pass explicit schemas into LLM-facing services and reject unsupported freeform paths for machine records.
```

# Verification
```yaml
verification:
  - id: VER-SFPC-001
    requirement_ids:
      - REQ-SFPC-001
      - REQ-SFPC-002
    invariant_ids:
      - INV-SFPC-001
    test: apps/api/test/run-routes.integration.test.ts
    assertion: HTTP handlers delegate to shared services and produce the same outcomes as CLI orchestration.
  - id: VER-SFPC-002
    requirement_ids:
      - REQ-SFPC-003
      - REQ-SFPC-004
    invariant_ids:
      - INV-SFPC-002
    test: packages/core/test/platform-core/repository-boundaries.integration.test.ts
    assertion: Orchestration code persists state only through repository interfaces and shared schemas.
  - id: VER-SFPC-003
    requirement_ids:
      - REQ-SFPC-005
    invariant_ids:
      - INV-SFPC-003
    test: packages/core/test/platform-core/run-lifecycle.integration.test.ts
    assertion: Valid state transitions succeed and invalid or duplicate transitions are rejected under retry.
  - id: VER-SFPC-004
    requirement_ids:
      - REQ-SFPC-006
    invariant_ids:
      - INV-SFPC-004
    test: packages/core/test/platform-core/structured-output.contract.test.ts
    assertion: LLM-facing services require explicit output schemas for machine-consumed results.
  - id: VER-SFPC-005
    requirement_ids:
      - REQ-SFPC-008
    invariant_ids:
      - INV-SFPC-005
    test: packages/core/test/platform-core/end-to-end-pipeline.integration.test.ts
    assertion: The canonical pipeline advances from acquisition planning through telemetry persistence without surface-specific forks.
```

# Testing Strategy
## Traceability Matrix

- `REQ-SFPC-001`, `REQ-SFPC-002`, `INV-SFPC-001` -> `apps/api/test/run-routes.integration.test.ts`
- `REQ-SFPC-003`, `REQ-SFPC-004`, `INV-SFPC-002` -> `packages/core/test/platform-core/repository-boundaries.integration.test.ts`
- `REQ-SFPC-005`, `INV-SFPC-003` -> `packages/core/test/platform-core/run-lifecycle.integration.test.ts`
- `REQ-SFPC-006`, `INV-SFPC-004` -> `packages/core/test/platform-core/structured-output.contract.test.ts`
- `REQ-SFPC-008`, `INV-SFPC-005` -> `packages/core/test/platform-core/end-to-end-pipeline.integration.test.ts`

## Integration Scenarios

- Create a run through both CLI and API surfaces and assert both delegate to the same service layer.
- Execute the full happy-path run lifecycle and assert that each phase transition persists exactly once.
- Retry run creation and provider-call persistence with the same idempotency key and assert no duplicate durable records appear.
- Force a repository failure during orchestration and assert the failure state is recorded without bypassing interfaces.
- Invoke an LLM-backed machine-consumed task without a schema and assert the service rejects the call.

## Test Files

- `apps/api/test/run-routes.integration.test.ts`
- `apps/cli/test/platform-core.commands.integration.test.ts`
- `packages/core/test/platform-core/end-to-end-pipeline.integration.test.ts`
- `packages/core/test/platform-core/repository-boundaries.integration.test.ts`
- `packages/core/test/platform-core/run-lifecycle.integration.test.ts`
- `packages/core/test/platform-core/structured-output.contract.test.ts`

# Source Text

The following source sections are copied verbatim from `specs/signal-farm.md` for subsystem fidelity during implementation.

## Summary

`signal-farm` is a service-backed novelty and idea-frequency farming system for AI, context engineering, and agentic engineering.

It exists to do five things well:

- collect candidate signals from multiple acquisition strategies
- normalize them into a shared canonical item model
- judge novelty and recurrence against a growing KB/KG baseline in Postgres
- route useful results into explicit human review queues rather than a flat digest feed
- accumulate replayable telemetry, evals, and goldens so later policy learning can optimize query, provider, and acquisition strategy choices

The system is intentionally designed so that:

- providers are candidate generators, not truth sources
- novelty is judged against the KB/KG, not by comparing providers to one another
- recurrence is a first-class signal, not a side effect of novelty detection
- APIs, CLIs, and future worker surfaces all call into the same contract-driven core
- Postgres is the system of record for operational state, graph state, replay state, eval state, and scoring state

This document is the implementation source of truth for `signal-farm`.

## Invariants

The following rules are mandatory and should be treated as design invariants, not suggestions:

1. Novelty and recurrence are always judged against a KB/KG snapshot, never against another provider’s live output.
2. Every run, replay, eval, and research iteration must bind to an explicit snapshot or snapshot timestamp boundary.
3. Every materially useful operation must be idempotent when retried with the same idempotency key.
4. API and CLI surfaces must remain thin over the same service interfaces.
5. Postgres access must remain behind repository interfaces.
6. Machine-consumed LLM outputs must use structured outputs when supported by the provider.
7. Facts, edges, labels, and feature records must be append-only and snapshot-addressable.
8. QMD semantic search and lexical search must both run before novelty or recurrence judgment.
9. Replay and eval workflows must be DB-backed.
10. Candidate policies in autoresearch loops must be compared against stable replayable baselines before promotion.

## Goals

- Detect genuinely novel items within tracked topics.
- Detect recurring or accelerating ideas that are becoming important even if they are not strictly novel.
- Preserve sufficient provenance to explain why something was surfaced.
- Support multiple acquisition strategies, including web search, feeds, curated sources, monitors, and experimental hypothesis generation.
- Track query, provider, and strategy performance over time.
- Attach extensible feature data to items, ideas, graph edges, and policy examples so later ML and RL systems can train on stable records without schema rewrites.
- Support explicit human review workflows.
- Support DB-backed replay for evals and golden creation with incremental cost.
- Support autoresearch-style loops where agents iterate on program files, prompts, and policies against replayable baselines.
- Support later bandits and RL on stable training data.

## Non-goals

- This is not a general web crawler.
- This is not a social dashboard product.
- This is not a generic ETL framework.
- This is not a pure vector-search system.
- This is not a provider-comparison benchmark where providers judge each other’s novelty.
- This is not a file-cache-based experimentation loop.

## Architecture

### System context

`signal-farm` should be implemented in TypeScript with Effect as the application runtime model.

The system is service-backed. The core business logic should live behind contracts and repository interfaces so it can be exposed through:

- an operator CLI
- an internal API
- future background workers
- future SDK or MCP surfaces

### Runtime boundaries

The initial project shape should follow this structure:

```text
signal-farm/
  apps/
    api/        # service boundary for HTTP or internal service calls
    cli/        # thin operator/client surface over the same contracts
  packages/
    contracts/  # Effect schemas, request/response contracts, enums, value objects
    core/       # orchestration, domain services, policies, queueing logic
    postgres/   # repository implementations and SQL-backed adapters
    providers/  # provider and acquisition adapters
  configs/      # topics, providers, budgets, prompts
  programs/     # operator-facing loop definitions
  sql/          # migrations and DDL
  .codex/skills/
  .claude/skills/
```

### Core boundaries

- `apps/api`
  - long-lived process boundary
  - eventually exposes HTTP routes, webhooks, health endpoints, and admin surfaces
  - contains no novelty logic beyond request orchestration
- `apps/cli`
  - thin command surface for local and automated operation
  - translates operator input into contract calls
  - does not contain domain logic
- `packages/contracts`
  - canonical boundary contracts
  - Effect schemas for external and cross-package payloads
  - stable types for items, labels, queues, policies, and replay
- `packages/core`
  - orchestration and domain services
  - repository interfaces
  - provider adapter interfaces
  - novelty, recurrence, review, and policy logic
- `packages/postgres`
  - concrete repository implementations
  - query plans, SQL, transactional boundaries
  - graph and vector retrieval
- `packages/providers`
  - search adapters
  - feed adapters
  - monitor adapters
  - replay-aware provider clients

### Service-backed rule

Every materially useful operation should be callable through a service interface first, with CLI and API acting as clients of those services.

This means:

- no business logic embedded directly in CLI commands
- no novelty or queueing logic embedded directly in route handlers
- no raw SQL in orchestration services
- no provider-specific response parsing outside provider adapters or normalizers

## Implementation principles

### Contract-driven development

Define contracts before implementation for:

- CLI command inputs and outputs
- API route inputs and outputs
- config file shapes
- provider request and normalized response shapes
- canonical item shapes
- graph node and edge metadata shapes
- novelty, recurrence, content-state, and review payloads
- replay and cassette record formats
- eval case and golden record formats

Use explicit Effect schemas for all external or cross-layer payloads. Do not allow silent JSON shape drift.

Where an LLM is expected to return machine-consumed JSON, use structured outputs or schema-constrained output modes whenever the provider supports them. Do not rely on freeform JSON prompting plus brittle post-hoc parsing when a structured-output path is available.

### Repository-driven development

All Postgres access must sit behind repository interfaces and concrete implementations.

At minimum, maintain separate repository seams for:

- runs and provider calls
- cassettes and replay records
- query execution and query performance
- canonical items and dedupe
- graph nodes and edges
- idea linking and frequency windows
- novelty labels and content-state labels
- review queues
- eval cases and scores
- provisional and confirmed goldens

The core domain layer should never depend directly on SQL text or query builders.

### Effect-oriented structure

Use Effect layers to model:

- repository services
- provider services
- acquisition services
- normalization services
- graph services
- novelty services
- recurrence services
- review queue services
- cassette and replay services
- eval services
- policy services

Keep side effects at the edges. The core novelty and recurrence logic should remain testable with mock repositories and mock provider adapters.

## End-to-end flow

Every candidate item should flow through the same high-level pipeline:

1. acquisition planning
2. candidate acquisition
3. normalization
4. strict dedupe
5. semantic dedupe
6. KB/KG evidence retrieval
7. novelty judgment
8. idea extraction and recurrence judgment
9. content-state and usefulness judgment
10. review queue assignment
11. digest or operator-facing summary generation
12. eval and golden sampling
13. telemetry persistence for policy learning

## Service interfaces

The core should expose service interfaces roughly like this:

```ts
export interface AcquisitionService {
  readonly planQueries: (input: PlanQueriesInput) => Effect.Effect<ReadonlyArray<QueryIntent>>
  readonly executeQueries: (input: ExecuteQueriesInput) => Effect.Effect<ReadonlyArray<CandidateItem>>
}

export interface NormalizationService {
  readonly normalizeCandidates: (
    candidates: ReadonlyArray<CandidateItem>,
  ) => Effect.Effect<ReadonlyArray<CanonicalItem>>
}

export interface DedupeService {
  readonly strictDedupe: (
    items: ReadonlyArray<CanonicalItem>,
  ) => Effect.Effect<ReadonlyArray<CanonicalItem>>
  readonly semanticDedupe: (
    items: ReadonlyArray<CanonicalItem>,
  ) => Effect.Effect<ReadonlyArray<CanonicalItem>>
}

export interface NoveltyService {
  readonly buildEvidenceBundle: (
    item: CanonicalItem,
  ) => Effect.Effect<EvidenceBundle>
  readonly judgeNovelty: (
    item: CanonicalItem,
    evidence: EvidenceBundle,
  ) => Effect.Effect<NoveltyJudgment>
}

export interface IdeaService {
  readonly linkIdeas: (item: CanonicalItem) => Effect.Effect<ReadonlyArray<IdeaLink>>
  readonly scoreIdeaFrequency: (
    topicId: TopicId,
  ) => Effect.Effect<ReadonlyArray<IdeaFrequencyWindow>>
}

export interface ReviewQueueService {
  readonly assignQueues: (input: AssignQueuesInput) => Effect.Effect<ReadonlyArray<ReviewQueueItem>>
}

export interface ReplayService {
  readonly recordProviderCall: (input: ProviderCallRecord) => Effect.Effect<void>
  readonly replayProviderCall: (key: CassetteKey) => Effect.Effect<ReplayResult>
}

export interface EvalService {
  readonly collectEvalCases: (runId: string) => Effect.Effect<ReadonlyArray<EvalCase>>
  readonly scoreEvalRun: (input: ScoreEvalRunInput) => Effect.Effect<EvalRunScore>
}

export interface FeatureService {
  readonly attachFeatures: (features: ReadonlyArray<FeatureRecord>) => Effect.Effect<void>
  readonly getFeaturesForTarget: (
    targetType: FeatureTargetType,
    targetId: string,
  ) => Effect.Effect<ReadonlyArray<FeatureRecord>>
}

export interface ResearchLoopService {
  readonly startIteration: (
    definitionId: string,
    candidateConfigVersion: string,
  ) => Effect.Effect<ResearchIteration>
  readonly evaluateIteration: (
    iterationId: string,
  ) => Effect.Effect<ResearchOutcome>
}
```

LLM-facing services should accept both:

- the prompt or policy inputs
- the explicit output schema required for structured parsing

Schema ownership belongs in `packages/contracts`, not inside ad hoc prompt strings.

## Repository interfaces

Repositories should be narrow, explicit, and domain-shaped.

```ts
export interface RunRepository {
  readonly createRun: (input: CreateRunInput) => Effect.Effect<RunRecord>
  readonly updateRunStatus: (input: UpdateRunStatusInput) => Effect.Effect<void>
  readonly getRun: (runId: string) => Effect.Effect<RunRecord | null>
}

export interface QueryRepository {
  readonly saveQueryIntents: (intents: ReadonlyArray<QueryIntent>) => Effect.Effect<void>
  readonly saveQueryExecutions: (executions: ReadonlyArray<QueryExecution>) => Effect.Effect<void>
  readonly getQueryPerformance: (topicId: TopicId) => Effect.Effect<ReadonlyArray<QueryPerformanceRecord>>
}

export interface CanonicalItemRepository {
  readonly upsertCanonicalItems: (items: ReadonlyArray<CanonicalItem>) => Effect.Effect<void>
  readonly getCanonicalItemsByIds: (ids: ReadonlyArray<string>) => Effect.Effect<ReadonlyArray<CanonicalItem>>
}

export interface GraphRepository {
  readonly upsertNodes: (nodes: ReadonlyArray<GraphNodeRecord>) => Effect.Effect<void>
  readonly upsertEdges: (edges: ReadonlyArray<GraphEdgeRecord>) => Effect.Effect<void>
  readonly queryNeighborhood: (input: GraphNeighborhoodQuery) => Effect.Effect<ReadonlyArray<GraphNodeRecord>>
}

export interface ReplayRepository {
  readonly saveCassette: (record: CassetteRecord) => Effect.Effect<void>
  readonly getCassette: (key: CassetteKey) => Effect.Effect<CassetteRecord | null>
  readonly recordReplayHit: (key: CassetteKey) => Effect.Effect<void>
}

export interface EvalRepository {
  readonly saveEvalCases: (cases: ReadonlyArray<EvalCase>) => Effect.Effect<void>
  readonly saveEvalScores: (scores: ReadonlyArray<EvalScoreRecord>) => Effect.Effect<void>
}

export interface FeatureRepository {
  readonly upsertFeatures: (features: ReadonlyArray<FeatureRecord>) => Effect.Effect<void>
  readonly getFeaturesForTarget: (
    targetType: FeatureTargetType,
    targetId: string,
  ) => Effect.Effect<ReadonlyArray<FeatureRecord>>
}

export interface ResearchRepository {
  readonly createLoopDefinition: (
    input: ResearchLoopDefinition,
  ) => Effect.Effect<ResearchLoopDefinition>
  readonly createIteration: (
    input: ResearchIteration,
  ) => Effect.Effect<ResearchIteration>
  readonly saveOutcome: (input: ResearchOutcome) => Effect.Effect<void>
}
```

## State machines

Long-running and replayable entities must have explicit state machines.

### Run state machine

```ts
export type RunStatus =
  | "pending"
  | "planned"
  | "acquiring"
  | "normalizing"
  | "labeling"
  | "queued_for_review"
  | "completed"
  | "failed"
  | "aborted"
```

Required transitions:

- `pending -> planned`
- `planned -> acquiring`
- `acquiring -> normalizing`
- `normalizing -> labeling`
- `labeling -> queued_for_review`
- `queued_for_review -> completed`
- any active state -> `failed`
- any active state -> `aborted`

### Provider call state machine

```ts
export type ProviderCallStatus =
  | "pending"
  | "in_flight"
  | "succeeded"
  | "failed"
  | "replayed"
```

### Research iteration state machine

```ts
export type ResearchIterationStatus =
  | "pending"
  | "running"
  | "accepted"
  | "rejected"
  | "aborted"
```

### Idempotency requirements

At minimum, the following operations require idempotency keys:

- run creation
- provider call recording
- cassette creation
- query execution persistence
- review queue item creation
- research iteration creation

Retries with the same idempotency key must not duplicate durable records.

## API and CLI surfaces

### CLI commands

The initial CLI should support commands like:

- `run-day`
- `plan-queries`
- `fetch`
- `dedupe`
- `novelty`
- `digest`
- `kb-ingest`
- `eval collect`
- `eval replay`
- `goldens propose`
- `goldens promote`
- `inspect query-performance`
- `inspect novelty-frontier`
- `inspect cassette-stats`

The CLI is a thin surface. It should call core services, not embed workflow logic.

### API surfaces

The API should eventually expose service-backed routes for:

- run creation and run status
- query planning
- acquisition execution
- novelty and recurrence inspection
- review queue access
- replay and eval operations
- admin and health endpoints

Route contracts should be derived from `packages/contracts`.

## Skills and project instructions

Project-specific skills should live in hidden project-scoped folders:

- `.codex/skills/auto-research/`
- `.codex/skills/novelty-farming/`
- `.claude/skills/auto-research/`
- `.claude/skills/novelty-farming/`

Do not use a top-level `skills/` directory for project-specific agent instruction assets.

Operator-facing program files live under:

- `programs/`

The program files are not decorative documentation. They are intended to act as the main editable research-control surface for autonomous and semi-autonomous loops, similar in spirit to `autoresearch`'s `program.md`, but adapted to the `signal-farm` policy and acquisition domain.

## Acceptance criteria

The implementation is only acceptable if:

- novelty judgments are KB/KG-relative, not provider-relative
- recurrence is modeled explicitly through idea nodes and windows
- edge metadata is queryable in the property graph
- QMD search and regex or keyword search both run before novelty or recurrence judgment
- replay, eval, and golden workflows are DB-backed
- fairness and replay bind to an explicit knowledge snapshot boundary
- facts, edges, labels, and feature records are append-only and snapshot-addressable
- retrieval behavior is defined by a first-class retrieval policy contract
- graph nodes and edges follow deterministic identity and merge rules
- run, provider call, and research iteration state machines are explicit
- query, provider, and strategy telemetry is persisted for later policy learning
- item, idea, edge, query, and policy-example feature capture is append-only and namespaced
- the system can persist and compare autoresearch-style iterations against replayable baselines
- machine-consumed LLM outputs use structured outputs whenever the provider supports them
- eval promotion uses temporal snapshot windows over append-only knowledge, not a single optimized replay slice
- API and CLI surfaces remain thin over the same contract-driven core
- repositories isolate Postgres access from orchestration services

## Defaults

- TypeScript + Effect is the implementation baseline
- Postgres is the system of record
- `pgvector` is the embedding layer
- service-backed architecture is the default shape
- novelty is judged against the KB/KG
- recurrence is a first-class signal
- pinned queries are operational inputs, not query-planning eval targets

# Open Questions
- [ ] Should the first implementation keep the current Python bootstrap surface as a temporary shim, or replace it immediately with the TypeScript + Effect layout described in the source text?
- [ ] Should idempotency tracking live inside the run and replay repositories initially, or should it have a dedicated repository seam from the first implementation pass?
