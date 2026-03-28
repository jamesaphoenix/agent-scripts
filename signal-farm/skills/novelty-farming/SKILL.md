---
name: novelty-farming
description: Use when linking signal-farm candidates into the KB/KG, retrieving evidence, labeling novelty and recurrence, assigning content-state buckets, and producing review queues. This skill teaches the KB-first judgment loop and requires QMD semantic search plus 3-5 regex or keyword searches in parallel.
---

# Novelty Farming

Use this skill when judging whether a candidate is novel, recurring, validated, or worth review.

## Core rules

- Novelty is relative to the KB/KG, not to other providers.
- Before labeling, build a bounded evidence bundle from:
  - QMD semantic search
  - 3-5 regex or keyword searches in parallel
  - graph neighborhood traversal
  - vector neighbors
- Distinguish novelty from usefulness.
- Distinguish recurrence from strict novelty.

## Output targets

Each item should end with:

- novelty label
- idea-frequency linkage or label
- content mode
- review value
- user utility
- validation status

## Review queues

Use the following queue intents:

- novel_now
- emerging_ideas
- validated_references
- watchlist
- archive

## Required references

- `../../specs/signal-farm.md`
- `../../configs/prompts/novelty-labeler.md`
- `../../configs/prompts/content-state.md`
- `../../configs/prompts/idea-linker.md`
