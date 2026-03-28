# signal-farm

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

### Program-definition contracts

`programs/` files are a first-class control surface and should have an explicit parseable shape, even if authored in Markdown.

```ts
export interface ProgramDefinition {
  readonly id: string
  readonly slug: string
  readonly title: string
  readonly objective: string
  readonly referencedConfigVersions: ReadonlyArray<string>
  readonly referencedPromptVersions: ReadonlyArray<string>
  readonly acquisitionPolicies: ReadonlyArray<string>
  readonly reviewPolicies: ReadonlyArray<string>
  readonly metadata: Record<string, unknown>
}
```

Precedence rule:

- contracts define legal shapes
- configs define baseline values
- program files select and compose versioned configs and policies
- ad hoc prompt text must not silently override typed baseline behavior

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

### Idea and recurrence contracts

```ts
export interface IdeaNode {
  readonly id: string
  readonly slug: string
  readonly title: string
  readonly topicId: TopicId
  readonly metadata: Record<string, unknown>
}

export interface IdeaLink {
  readonly canonicalItemId: string
  readonly ideaId: string
  readonly confidence: number
  readonly linkType: "supports_existing_idea" | "creates_new_idea"
  readonly rationale: string
}

export interface IdeaFrequencyWindow {
  readonly ideaId: string
  readonly topicId: TopicId
  readonly windowDays: 7 | 30 | 90
  readonly rawMentionCount: number
  readonly distinctSourceCount: number
  readonly distinctProviderCount: number
  readonly distinctDomainCount: number
  readonly acceleration: number | null
  readonly burstiness: number | null
  readonly sourceAuthorityWeightedFrequency: number | null
  readonly label: IdeaFrequencyLabel
  readonly rationale: string
}
```

### Review queue contracts

```ts
export type ReviewQueueName =
  | "novel_now"
  | "emerging_ideas"
  | "validated_references"
  | "watchlist"
  | "archive"

export interface ReviewQueueItem {
  readonly id: string
  readonly queueName: ReviewQueueName
  readonly canonicalItemId: string | null
  readonly ideaId: string | null
  readonly topicId: TopicId
  readonly priorityScore: number
  readonly reasonCodes: ReadonlyArray<string>
  readonly metadata: Record<string, unknown>
}
```

### Replay, eval, and golden contracts

```ts
export type FreshnessClass =
  | "immutable"
  | "ttl_daily"
  | "ttl_windowed"

export interface CassetteKey {
  readonly providerId: string
  readonly endpointType: string
  readonly requestFingerprint: string
  readonly modelOrEngine: string | null
  readonly promptConfigHash: string | null
  readonly freshnessClass: FreshnessClass
}

export interface EvalCase {
  readonly id: string
  readonly evalDomain:
    | "query_planning_eval"
    | "retrieval_fairness_eval"
    | "dedupe_eval"
    | "novelty_eval"
    | "idea_frequency_eval"
    | "digest_eval"
  readonly subjectIds: ReadonlyArray<string>
  readonly inputPayload: Record<string, unknown>
  readonly expectedPayload: Record<string, unknown> | null
  readonly metadata: Record<string, unknown>
}

export interface GoldenRecord {
  readonly id: string
  readonly tier: "provisional" | "confirmed"
  readonly goldenType: string
  readonly subjectIds: ReadonlyArray<string>
  readonly verdict: string | null
  readonly evidencePayload: Record<string, unknown>
  readonly metadata: Record<string, unknown>
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

### Research-loop contracts

`signal-farm` should support autoresearch-style loops where the human mostly edits the program file and the agents iterate on policy and prompt behavior against replayable evaluations.

```ts
export interface ResearchLoopDefinition {
  readonly id: string
  readonly slug: string
  readonly programPath: string
  readonly objective: string
  readonly baselineConfigVersion: string
  readonly evaluationBudgetClass: string
  readonly metadata: Record<string, unknown>
}

export interface ResearchIteration {
  readonly id: string
  readonly loopDefinitionId: string
  readonly parentIterationId: string | null
  readonly proposedBy: "human" | "agent"
  readonly changedArtifacts: ReadonlyArray<string>
  readonly hypothesis: string
  readonly candidateConfigVersion: string
  readonly status: "pending" | "running" | "accepted" | "rejected" | "aborted"
  readonly metadata: Record<string, unknown>
}

export interface ResearchOutcome {
  readonly iterationId: string
  readonly baselineConfigVersion: string
  readonly candidateConfigVersion: string
  readonly evalDomainScores: Record<string, number>
  readonly rewardEstimate: number | null
  readonly keepDecision: "keep" | "discard" | "needs_review"
  readonly rationale: string
}
```

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

## Novelty model

Novelty is always relative to the KB/KG evidence bundle.

Labels:

- `novel_topic`
- `novel_angle`
- `material_update`
- `known_topic_low_novelty`
- `duplicate`

Novelty labels must be stored per:

- labeler model
- prompt config version
- retrieval policy version
- comparison window

Never overwrite old labels.

## Idea-frequency model

Recurrence is a first-class signal.

Each canonical item may link to one or more stable idea nodes.

For each idea/topic/window, compute:

- raw mention count
- distinct source count
- distinct provider count
- distinct domain count
- 7-day count
- 30-day count
- acceleration score
- burstiness score
- source-authority weighted frequency
- validated-source weighted frequency

Frequency labels:

- `emerging`
- `recurring`
- `spiking`
- `persistent_background`
- `fading`

Important rule:

- a high-frequency idea may be low novelty but still important
- a novel item may have low frequency but still matter

## Content-state and review model

Novelty does not determine usefulness.

Every item gets:

- `content_mode`
  - `news`
  - `evergreen`
  - `validated_reference`
  - `unclear`
- `review_value`
  - `review_now`
  - `review_if_time`
  - `archive_only`
- `user_utility`
  - `actionable`
  - `contextual`
  - `interesting_but_low_value`
  - `not_useful`
- `validation_status`
  - `unverified`
  - `partially_validated`
  - `validated`
  - `contradicted`

### Review queues

Use explicit queues:

- `novel_now`
- `emerging_ideas`
- `validated_references`
- `watchlist`
- `archive`

Queue intent:

- `novel_now`
  - novel or material-update items with high review urgency
- `emerging_ideas`
  - recurring or accelerating ideas even if low novelty
- `validated_references`
  - official or trustworthy sources worth promoting into the KB
- `watchlist`
  - items or ideas that are recurring but not yet clearly valuable
- `archive`
  - low-utility or low-priority results retained for history but not surfaced prominently

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

## Replay, evals, and goldens

Use DB-backed cassettes and replay for:

- eval collection
- eval replay
- golden collection
- prompt comparisons
- provider and strategy comparisons

Do not rely on file-backed eval caches.

### Cassette model

Each cassette key includes:

- provider id
- endpoint type
- request fingerprint
- model or engine
- prompt config hash where relevant
- freshness class

Freshness classes:

- `immutable`
- `ttl_daily`
- `ttl_windowed`

### Replay determinism requirements

Replay semantics must define:

- whether a cassette replay preserves original timestamps or replay timestamps
- whether downstream scoring sees original cost and latency or replay-adjusted values
- how replay misses are handled
- whether mixed live-plus-replay runs are allowed

The initial implementation should default eval and golden workflows to replay-only unless `allow_refresh` is explicitly enabled.

### Eval domains

- `query_planning_eval`
- `retrieval_fairness_eval`
- `dedupe_eval`
- `novelty_eval`
- `idea_frequency_eval`
- `digest_eval`

### Golden tiers

- `provisional`
- `confirmed`

Goldens should retain:

- subject ids
- evidence payload
- verdict
- provenance
- rubric version

### Temporal snapshot evaluation and promotion rules

The primary eval model should use the append-only KB/KG itself rather than hand-maintained dataset tiers.

Required approach:

- bind every eval run to an explicit `snapshotAsOf`
- define eval cohorts as time windows over append-only records
- compare candidate policies across multiple disjoint historical windows, not a single replay slice
- use `knowledgeAvailableAt` as the leakage boundary for all retrieval and scoring

Promotion rule:

- a candidate policy may auto-promote only if it improves on required metrics across the configured replay windows
- promotion must be based on consistent performance across multiple temporal cohorts, not a single optimized window
- optional locked suites may exist later, but they are an added safeguard rather than the primary model

## Autoresearch loop compatibility

`signal-farm` should support autoresearch-style loops as a first-class operating mode.

The core idea is:

- the human primarily edits program files and high-level research instructions
- the agent proposes prompt, policy, routing, or feature changes
- the system evaluates candidate changes against replayable baselines
- the outcome is accepted or rejected based on explicit eval domains and reward estimates

### Core autoresearch principles adapted for signal-farm

- the editable human surface should be small and legible
- the program file is a first-class control surface
- research loops should operate on a bounded budget
- comparisons should be made against stable replayable baselines where possible
- candidate changes should be accepted or discarded based on measured outcomes, not vibes

### What agents should be allowed to modify in loops

The first loop types should focus on:

- prompt configs
- query expansion logic
- retrieval policy configs
- provider-routing policies
- queue scoring policies
- feature extraction policies
- review-threshold policies

These are safer and more comparable than letting agents rewrite the entire system at first.

### What should remain fixed in most loops

Keep these relatively stable unless a human explicitly opens them up:

- canonical item contract
- replay and cassette semantics
- core graph schema
- eval domain definitions
- goldens tier semantics

Add one more protected class:

- locked benchmark and holdout membership

### Research loop persistence

Every iteration should persist:

- changed artifacts
- candidate config version
- hypothesis
- eval domain scores
- reward estimate
- keep or discard decision
- rationale

This makes the research process itself queryable and trainable.

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
