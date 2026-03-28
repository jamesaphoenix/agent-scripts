---
name: design-doc
description: Generate a detailed design document via `tx doc add design`. Covers architecture, interfaces, data model, invariants, failure modes, verification, and testing strategy. References plan via file path instead of embedding. Plan lives in ~/.claude/plans/<name>.md. Reads companion PRD automatically to map EARS requirements to invariants. Output lands in specs/design/<name>.md.
argument-hint: <feature-or-component-name>
---

# Generate Design Document

Create a comprehensive technical design document using the tx doc primitive. Design docs specify HOW the system implements requirements, with traceable invariants and a concrete testing strategy.

Before drafting from memory, fetch the canonical tx scaffold for the target design doc and use that as the minimum valid schema. Run `tx doc add design <name> --title "<title>"` if the doc does not exist yet, then run `tx doc show <name> --md`. That output is the baseline shape to preserve. Add structure on top of it; do not invent frontmatter fields or remove tx-required top-level sections.

When migrating or decomposing an existing spec into subsystem docs, retain all material information. Move the source detail across rather than summarizing it away, and add new headers or subheaders wherever needed to preserve the original nuance. The document shape is flexible, and extra headings are allowed and encouraged when they help keep source detail intact.

Do not default to YAML for `# Interfaces` or `# Data Model`. Keep YAML confined to parser-managed sections such as `ears_requirements` and `invariants`; the main design narrative should stay in normal markdown with whatever subsection structure the source material requires.

**Design Doc + PRD are companions.** A PRD defines WHAT and WHY. A design doc defines HOW. If a PRD exists for this feature, the design doc reads it automatically and maps every `must`-priority EARS requirement to an invariant + verification entry.

## Naming Discipline

- tx assigns each doc an immutable `doc_id`. Human `name` slugs only need to be unique within their doc kind.
- Use distinct companion names such as `<feature>-prd` and `<feature>-design`.
- If a slug is already taken by another doc kind, rename the design doc rather than reusing the same name.

## Migration Guidance

- When migrating existing markdown into tx-managed docs, preserve the source wording first, then normalize structure.
- Do not collapse source material into fewer headings if that removes detail. Prefer adding more sections, subsections, or appendix blocks to carry all material information across.
- When decomposing one large spec into subsystem docs, distribute the original content across the subsystem docs with full fidelity. Summarize only after the source meaning has been preserved in the destination documents.
- If you extract sections programmatically, use a fence-aware parser. Headings inside fenced code blocks are content, not section boundaries.

## Example Reference

Read [references/contextual-prompt-bandit-subsystem-design-example.md](references/contextual-prompt-bandit-subsystem-design-example.md) before drafting when you need a concrete example of a strong subsystem design.

That example is useful because it keeps:

- a sharp owns / does-not-own boundary
- structural identity rules separate from runtime context and outcomes
- concrete storage tables, APIs, and algorithms in one document
- explicit invariants and operational behaviors instead of vague architecture prose

If the user's source material already contains this much concrete detail, preserve it. Do not flatten it into a lightweight summary just because the tx scaffold starts smaller.

## Workflow State Machine

```
START
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 0: PLAN GATE                                    │
│                                                      │
│ Is there an active plan in this conversation?        │
│                                                      │
│ ├─ YES → Save plan to `~/.claude/plans/<name>.md` if not       │
│ │        already saved. Reference that plan from the           │
│ │        `# Plan` section.                            │
│ │        → Continue to Step 0.5                      │
│ │                                                    │
│ └─ NO  → Tell the user to run /plan first.           │
│          If enough detail provided, generate plan,   │
│          save to `~/.claude/plans/<name>.md`.                  │
│          → Continue to Step 0.5                      │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 0.5: CHECK FOR COMPANION PRD                    │
│                                                      │
│ Run: tx doc list --kind prd                          │
│                                                      │
│ ├─ PRD exists for this feature?                      │
│ │   → tx doc show <prd-name> --md                    │
│ │   → Extract ALL EARS requirements                  │
│ │   → Each `must` EARS req MUST get an invariant     │
│ │     and a verification entry in this design doc    │
│ │   → Set `implements: <prd-name>` in frontmatter   │
│ │   → Continue to Step 1                             │
│ │                                                    │
│ └─ No companion PRD?                                 │
│     → Continue to Step 1 (design doc stands alone)   │
│     → Suggest creating PRD after: /prd <name>        │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 1: SCAFFOLD via tx                              │
│                                                      │
│ tx doc add design <name> --title "<title>"           │
│ ├─ SUCCESS → Continue to Step 2                      │
│ └─ FAIL (exists) → Edit existing doc                 │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 2: DEEP CONTEXT GATHERING                       │
│                                                      │
│ Read: ARCHITECTURE.md, QUALITY.md, CLAUDE.md,        │
│       domain code, schema.ts, effect-schemas,        │
│       API routes, workflows, activities,             │
│       existing designs (tx doc list --kind design)   │
│ → Continue to Step 3                                 │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 3: FILL DOCUMENT                                │
│                                                      │
│ Write `# Plan` first (reference to plan file from     │
│ Step 0).                                             │
│ Then fill all sections from plan + PRD + codebase.   │
│                                                      │
│ MINIMUM THRESHOLDS:                                  │
│   - Invariants: ≥ 5                                  │
│   - Failure modes: ≥ 3                               │
│   - Verification entries: ≥ 5                        │
│   - Integration test files: ≥ 2 (HARD REQUIREMENT)   │
│   - Unit test files: ≥ 1 (recommended, not hard)     │
│   - Sequence diagrams: ≥ 2 (happy + error)           │
│   - Design decisions: ≥ 1                            │
│                                                      │
│ Integration tests are the primary verification       │
│ mechanism. Unit tests complement but do not replace   │
│ integration tests.                                   │
│                                                      │
│ RULE: No section may be left as a template/stub.     │
│                                                      │
│ COMPREHENSIVENESS: The design doc must cover EVERY   │
│ item from the plan. Every implementation step,       │
│ constraint, risk, and decision in the plan must      │
│ appear in the design doc with full technical detail.  │
│ The design doc is the single source of truth for HOW │
│ the feature is built — it should be detailed enough  │
│ that an engineer can implement from it alone.        │
│                                                      │
│ MIGRATIONS: When converting a plan or larger source  │
│ document into a design doc, preserve every critical  │
│ contract, endpoint, schema, failure mode, testable   │
│ behavior, and implementation constraint. Do not      │
│ collapse or silently omit source information.        │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 4: SELF-AUDIT                                   │
│                                                      │
│ Check:                                               │
│ ├─ Every plan item captured in a section?            │
│ ├─ Every PRD `must` EARS req has an invariant?       │
│ ├─ Every invariant has a verification entry?         │
│ ├─ Minimums met?                                     │
│ ├─ No stubs/placeholders?                            │
│ ├─ All diagrams complete?                            │
│ └─ Plan file exists at frontmatter path and is       │
│    consistent with doc sections?                     │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 5: VALIDATE                                     │
│                                                      │
│ tx spec lint                                         │
│ ├─ PASS → Continue to Step 6                         │
│ └─ WARN/FAIL → Fix, re-validate                     │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 5.5: SYNC PLAN FILE                             │
│                                                      │
│ Read the plan file from frontmatter `plan:` path.    │
│ Compare with what the doc now contains.              │
│ UPDATE the plan file to incorporate:                 │
│   - Architecture decisions, component inventory      │
│   - Interface contracts, data model details          │
│   - Invariants, failure modes, error handling        │
│   - Implementation sequence, testing strategy        │
│ The plan file must reflect the FULL current state    │
│ of the feature — not just the initial draft.         │
│ This is a MANDATORY step, not optional.              │
└─────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ Step 6: DISCOVER + LINK + REPORT                     │
│                                                      │
│ tx spec discover --doc <name>                        │
│ tx doc link <prd> <design> (if PRD exists)           │
│ tx doc show <name>                                   │
│ tx spec gaps --doc <name>                            │
│ Print summary                                        │
└─────────────────────────────────────────────────────┘
  │
  ▼
DONE
```

## Step 0 — Plan Gate

**The plan is the primary input.** Check for plan content in the conversation:

- The plan is saved as a standalone file at `~/.claude/plans/<name>.md` (relative to repo root).
- If a plan already exists in the conversation, write it to that file.
- If a plan file already exists at that path, read it instead.
- If no plan and vague request, ask user to run `/plan` first.

Do not add a `plan:` field to frontmatter. The `# Plan` section contains a reference link plus a brief summary, not the full verbatim content.

## Step 0.5 — Check for Companion PRD

```bash
tx doc list --kind prd
```

If a PRD exists for this feature:
1. Read it: `tx doc show <prd-name> --md`
2. Extract every EARS requirement
3. Every `must`-priority EARS requirement MUST become:
   - An invariant in `invariants:` YAML block
   - A verification entry in `verification:` YAML block
4. Set `implements: <prd-name>` in frontmatter

If no PRD exists, the design doc stands alone. Suggest creating one after.

## Step 1 — Scaffold via tx

```bash
tx doc add design $ARGUMENTS --title "<Human-Readable Title>"
```

Creates `specs/design/<name>.md`. If exists, edit instead.

Immediately after scaffolding or opening an existing design doc, fetch the canonical markdown shape:

```bash
tx doc show $ARGUMENTS --md
```

Treat that output as the minimal schema for the document. Keep the required frontmatter keys and required top-level sections intact, and add richer subsections whenever the source material carries more detail than the scaffold alone.

## Step 2 — Deep Context Gathering

Read these files:

- `docs/ARCHITECTURE.md` — architecture + DDD structure
- `docs/QUALITY.md` — all invariants, governance rules
- `CLAUDE.md` — stack, conventions
- Companion PRD (from Step 0.5)
- Domain code: `packages/core/src/domains/`
- Database schema: `packages/infra/db/src/schema.ts`
- Effect schemas: `packages/infra/db/src/effect-schemas/`
- API routes: `apps/api/src/`
- Workflows: `apps/worker/src/workflows.ts`
- Activities: `apps/worker/src/activities.ts`
- Existing designs: `tx doc list --kind design`

## Step 3 — Fill the Document

### Required Frontmatter (already generated by tx)

```yaml
---
kind: spec
spec_type: design
name: <name>
title: "<title>"
status: draft
version: 1
owners:
  - <team-or-person>
summary: Technical approach for <title>
domain: <product-area>
tags:
  - design
depends_on: []
supersedes: []
implements: <prd-name-or-null>
last_reviewed_at: <YYYY-MM-DD>
---
```

Update `owners`, `summary`, `domain`, `tags`, `depends_on`, `implements`.

### Body Structure — ALL sections MUST have real content

**`# Plan` comes first (as a reference to the plan file). Then all technical sections. No section may be a stub.**

Design docs should favor rich subsections under `# Interfaces` and `# Data Model`. Use `interfaces:` YAML only as an optional appendix for compact machine-readable summaries, not as the primary shape of the section. Add extra headings whenever needed to preserve source detail; the shape is flexible by design.

Keep YAML for parser-managed sections such as `ears_requirements` and `invariants`. Do not turn interface descriptions, repository seams, workflows, or database schemas into YAML just because a fenced block seems convenient.

**If a companion PRD exists, every `must` EARS requirement maps to an invariant + verification entry.**

```markdown
# Plan

> Full plan: [~/.claude/plans/<name>.md](../~/.claude/plans/<name>.md)

<2-3 sentence summary of what the plan covers. The full plan lives in the file referenced above.>

# Summary

2-3 sentences on design approach and key technical decisions.

# Architecture

## System Context

Where this feature sits in the existing system. Reference `docs/ARCHITECTURE.md`.

## Component Diagram

```
┌──────────────────────────────────────────────────────┐
│                    API Layer                          │
│  ┌──────────────────────────────────────────┐       │
│  │  apps/api [MOD]                          │       │
│  │  ├── routes/<domain>.ts [NEW]            │       │
│  └──────────────────┬───────────────────────┘       │
└─────────────────────┼────────────────────────────────┘
                      ▼
┌──────────────────────────────────────────────────────┐
│                  Domain Layer                         │
│  ┌──────────────────────────────────────────┐       │
│  │  packages/core/src/domains/<domain>/ [NEW]│       │
│  │  ├── domain/     [NEW]                    │       │
│  │  ├── ports/      [NEW]                    │       │
│  │  ├── application/ [NEW]                   │       │
│  │  └── adapters/   [NEW]                    │       │
│  └──────────────────┬───────────────────────┘       │
└─────────────────────┼────────────────────────────────┘
                      ▼
┌──────────────────────────────────────────────────────┐
│              Infrastructure Layer                     │
│  ┌─────────────┐                                    │
│  │ infra/db    │                                    │
│  │ [MOD]       │                                    │
│  └─────────────┘                                    │
└──────────────────────────────────────────────────────┘
```

Mark: `[NEW]`, `[MOD]`, or unmarked.

## Component Inventory

| Component | Package/App | Responsibility | New/Modified |
|-----------|-------------|---------------|--------------|

## Design Decisions

**MINIMUM: ≥ 1 decision with ADR-lite format.**

### Decision 1: <Title>

**Context:** Why needed.
**Options:**

| Option | Pros | Cons |
|--------|------|------|

**Decision:** Option X because <reasoning>.
**Consequences:** What follows.

# Interfaces

Design the implementation seams first. Prefer a structure like:

## Runtime Contracts

## Types

Request/response schemas, value objects, shared contract types, and payload shapes used across layers.

## Context

Effect services, request context, tenant context, and ambient dependencies required by the subsystem.

## Repositories

Persistence boundaries, repository responsibilities, and the operations the domain/application layers depend on.

## Services

Application services, orchestration services, and policy services that coordinate the subsystem.

## Workflows

Temporal workflows, queue jobs, retry behavior, and durable control-flow boundaries.

## Events

Emitted or consumed event payloads, including versioning and causal relationships.

Use `interfaces:` only as an optional appendix when a compact machine-readable contract summary is useful.

## Endpoints

Only include this section when the design exposes HTTP/REST endpoints.

| Method | Path | Request Schema | Response Schema | Auth | Permission |
|--------|------|---------------|----------------|------|-----------|

### Request/Response Schemas

```typescript
import { Schema } from 'effect'
export const Create<X>Request = Schema.Struct({ /* fields */ })
export const Create<X>Response = Schema.Struct({ /* fields */ })
```

### Error Responses

| Status | Error Code | Condition | Response Body |
|--------|-----------|-----------|--------------|
| 400 | `VALIDATION_ERROR` | Invalid request body | `{ error, details }` |
| 401 | `UNAUTHORIZED` | Missing/expired token | `{ error }` |
| 403 | `FORBIDDEN` | Insufficient permissions | `{ error }` |
| 404 | `NOT_FOUND` | Resource doesn't exist | `{ error }` |
| 409 | `CONFLICT` | Duplicate / state conflict | `{ error, details }` |

## Port Contracts

```typescript
export interface <Domain>Repository {
  create(input: Create<X>Input): Effect.Effect<X, <Error>>
  findById(id: string): Effect.Effect<X | null, <Error>>
  findMany(filter: <Filter>): Effect.Effect<readonly X[], <Error>>
  update(id: string, input: Update<X>Input): Effect.Effect<X, <Error>>
  remove(id: string): Effect.Effect<void, <Error>>
}
```

## Event Payloads (if applicable)

```typescript
export interface <Domain><Verb>EventPayload { /* fields */ }
export const <Domain><Verb>EventPayloadSchema = Schema.Struct({ /* fields */ })
```

# Data Model

The data model must show **actual schemas**, not just table or collection names. Use concrete subsections so the structure mirrors the source material.

## Database Schema

Show the canonical SQL or schema definitions here. Include table definitions, indexes, foreign keys, constraints, and any partitioning or retention notes.

## Types

Show typed representations of persisted state and schema-adjacent contracts used by the application layer.

## Derived Models

Show read models, projections, aggregates, and state transitions derived from the canonical tables.

## Persistence Schemas

```sql
CREATE TABLE <table_name> (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_<table>_<column> ON <table_name>(<column>);
```

## Contract Schemas

```typescript
export const <Entity>Schema = Schema.Struct({
  id: Schema.String,
  // ...
})

export const <CreateEntityRequest>Schema = Schema.Struct({
  // ...
})

export const <EntityResponse>Schema = Schema.Struct({
  // ...
})
```

## Relationships, Indexes, and Constraints

- Cardinality and ownership rules
- Unique constraints and conflict rules
- Sort/filter indexes
- Retention, archival, or audit-trail constraints
- Derived read models or materialized views when relevant

## State and Lifecycle Notes

- State transitions
- Reservation/expiry windows
- Derived counters or balances
- Idempotency keys and uniqueness boundaries

## Factories / Fixtures

```typescript
export const create<Table>Factory = (overrides?: Partial<Table>): Table => ({ /* defaults */ })
```

## Entity Relationship Diagram

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ organizations │────<│    teams      │────<│   <entity>   │
│              │  1:N │              │  1:N │              │
└──────────────┘     └──────────────┘     └──────────────┘
```

# Invariants

**MINIMUM: ≥ 5 invariants. If companion PRD exists, every `must` EARS req MUST map to an invariant.**

```yaml
invariants:
  - id: INV-<SCOPE>-001
    statement: <what must be true>
    enforcement: <lint script | test | pgTAP>
    traces_to: REQ-<SCOPE>-001  # link to PRD requirement if applicable
```

# Failure Modes

**MINIMUM: ≥ 3 failure modes.**

```yaml
failure_modes:
  - id: FM-<SCOPE>-001
    trigger: <what causes failure>
    impact: <user/system effect>
    detection: <logs, metrics, alerts>
    mitigation: <automatic response>
    recovery: <human intervention>
```

# Verification

**MINIMUM: ≥ 5 entries. Every invariant MUST have a verification entry.**

```yaml
verification:
  - invariant: INV-<SCOPE>-001
    test_file: <path>
    test_name: "description [INV-<SCOPE>-001]"
    type: unit | integration | lint | pgtap
```

# Sequence Diagrams

**MINIMUM: ≥ 2 (happy path + at least one error path).**

## Happy Path: <Operation>

```
Client              API                   Core                  DB                   Worker
  │                  │                     │                    │                     │
  │ POST /api/<x>    │                     │                    │                     │
  │─────────────────▶│                     │                    │                     │
  │                  │ validate + auth      │                    │                     │
  │                  │ create<X>(input)     │                    │                     │
  │                  │────────────────────▶│                    │                     │
  │                  │                     │ BEGIN TXN           │                     │
  │                  │                     │───────────────────▶│                     │
  │                  │                     │ INSERT + event      │                     │
  │                  │                     │───────────────────▶│                     │
  │                  │                     │ COMMIT             │                     │
  │                  │                     │───────────────────▶│                     │
  │                  │◀────────────────────│                    │                     │
  │ 201 Created      │                     │                    │                     │
  │◀─────────────────│                     │                    │ poll + dispatch      │
  │                  │                     │                    │◀────────────────────│
```

## Error Path: <Failure>

```
Client              API
  │ POST /api/<x>    │
  │─────────────────▶│
  │                  │ <failure point>
  │ <status code>    │
  │◀─────────────────│
```

# Testing Strategy

**Integration tests are the primary verification mechanism and a HARD REQUIREMENT. Unit tests are recommended but secondary.**

The testing strategy must be VERY thorough. Cover:
- requirement-to-test and invariant-to-test traceability
- happy paths and failure paths for every critical interface
- auth, permission, and validation behavior where applicable
- idempotency, retries, duplicate delivery, or concurrency/race conditions where relevant
- downstream dependency failures, timeouts, and degraded-mode behavior
- migration, backfill, and data-integrity checks when persistence changes
- exact test files, fixtures, and concrete assertions rather than vague “add tests” bullets
- every critical behavior, failure mode, and constraint preserved from the source plan or larger migration document
- explicit coverage for every REST endpoint, event contract, queue boundary, and state transition that the design introduces

## Integration Tests

**HARD REQUIREMENT: ≥ 2 integration test files listed. Design docs MUST NOT be considered complete without integration tests.**

| Test File | What It Covers | Invariants |
|-----------|---------------|------------|

### Integration Test Patterns

```typescript
describe('<Domain> API', () => {
  it('returns 401 for unauthenticated [INV-<SCOPE>-003]', async () => { /* ... */ })
  it('creates resource with valid input', async () => { /* ... */ })
})
```

## Unit Tests (recommended, not hard requirement)

| Test File | What It Covers | Invariants |
|-----------|---------------|------------|

### Unit Test Patterns

```typescript
describe('<Entity>', () => {
  it('creates valid entity [INV-<SCOPE>-001]', () => { /* ... */ })
  // @spec REQ-<SCOPE>-001
  it('rejects invalid input', () => { /* ... */ })
})
```

## Database Contract Tests (pgTAP)

| Test File | What It Covers |
|-----------|---------------|

## Test Annotation Convention

```typescript
it('description [INV-<SCOPE>-001]', () => { ... })  // preferred
// @spec INV-<SCOPE>-001                              // alternative
```

# Migration Strategy

## Database Migration

**File:** `packages/infra/db/migrations/NNNN_<description>.sql`

## Rollback Plan

| Step | Action | Verification |
|------|--------|-------------|

# Security Considerations

| Concern | Mitigation | Enforcement |
|---------|-----------|-------------|

# Cross-Cutting Concerns

## Observability
## Data Retention

# Implementation Sequence

```
Phase 1: Domain Foundation
  ├── <specific file paths>

Phase 2: Infrastructure
  ├── <specific file paths>

Phase 3: Application Layer
  ├── <specific file paths>

Phase 4: API Layer
  ├── <specific file paths>

Phase 5: Worker (if domain events)
  ├── <specific file paths>

Phase 6: Quality Gates
  ├── pnpm lint && pnpm type-check && pnpm test && pnpm test:integration
  ├── tx spec discover --doc <name>
  └── tx spec fci --doc <name>
```

# Open Questions

- [ ] Questions requiring design input
```

## Step 4 — Self-Audit

Re-read the plan file and verify:
1. **Comprehensiveness check**: Read the plan file. For EVERY item (implementation steps, constraints, risks, decisions), confirm it has full technical detail in the design doc. The design doc must be detailed enough to implement from alone.
2. **Migration coverage check**: If this doc came from a larger source document, confirm every meaningful source section has been preserved, relocated, or explicitly superseded. No silent drops.
3. If PRD exists: every `must` EARS req has an invariant. Every invariant has a verification entry.
4. HARD: ≥2 integration test files (blocking requirement). SOFT: ≥1 unit test file (recommended).
5. Minimums met: ≥5 invariants, ≥3 failure modes, ≥5 verifications, ≥2 sequence diagrams, ≥1 decision.
6. Testing strategy is very thorough: traceability, happy path, failure path, validation/auth, dependency failures, concurrency/idempotency, endpoint coverage, and data integrity are all covered where relevant.
7. No stubs, no empty tables, no "..." placeholders.
8. All diagrams complete.
9. Implementation sequence lists real file paths.
10. Verify plan file exists at the path in frontmatter and its content is consistent with the doc sections.

## Step 5 — Validate

```bash
tx spec lint
```

Treat schema and parser errors as blocking. Coverage-oriented warnings after generation, such as unlinked tasks or invariants without tests yet, should be surfaced separately from structural doc errors.

## Step 5.5 — Sync Plan File (MANDATORY)

After filling and validating the doc, **update the plan file** at the `plan:` frontmatter path to reflect everything the design doc surfaced. The plan file must be the living source of truth — not a stale initial draft.

What to add to the plan file:
- Architecture decisions and their rationale
- Component inventory and file paths
- Interface contracts (HTTP routes, queues, events, and Effect service boundaries)
- Data model details (tables, indexes, constraints)
- Implementation sequence with specific file paths
- Invariants, failure modes, and error handling strategies
- Testing strategy and test file locations

Read the current plan file, merge in the new information, and write it back. Preserve the plan's structure but ensure it now covers the full technical design.

## Step 6 — Discover, Link & Report

```bash
tx spec discover --doc <name>
tx doc link <prd> <design>       # if PRD exists
tx doc show <name>
tx spec gaps --doc <name>
```

## After Generation

1. Print output path (`specs/design/<name>.md`).
2. Summarize: component count, invariant count, failure mode count, verification count, test file count.
3. If PRD exists, show EARS→invariant mapping coverage.
4. List open questions.
5. Run `tx spec lint`.
6. If the plan file is modified later, update the `# Plan` summary and derived sections in this doc. If this doc's scope changes, update the plan file to stay consistent.
