---
name: auto-research
description: Use when running signal-farm query planning, retrieval, and provider routing for AI, context engineering, and agentic engineering topics. This skill teaches an agent how to handle seeded, expanded, and pinned queries, how to respect budget stages, and how to prefer DB-backed replay for eval and golden workflows.
---

# Auto Research

Use this skill when operating `signal-farm` as a research agent.

## Core rules

- Providers generate candidates; they do not decide novelty.
- Run pinned queries when enabled, but keep them out of query-planning evals.
- Respect budget stages from `configs/budgets.yaml`.
- Prefer DB-backed cassette replay for evals and golden collection.
- Keep provider-vs-provider comparisons out of novelty logic.

## Workflow

1. Read `configs/topics.yaml`, `configs/providers.yaml`, and `configs/budgets.yaml`.
2. Merge seeded, expanded, and pinned queries.
3. Route native search calls through direct providers.
4. Route non-search judgments through OpenRouter by default.
5. Persist run metadata so query and provider performance can be scored later.

## Required references

- `../../specs/signal-farm.md`
- `../../programs/daily-news-v1.md`

Read those files before making material workflow changes.
