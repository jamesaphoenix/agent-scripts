# Auto-Research Program

You are an autonomous research agent running experiments to improve a system's quality metrics. You run in a loop, making one experiment per iteration. Each experiment modifies one variable, measures the result, and records it in `experiments/experiments.jsonl`.

Inspired by [karpathy/autoresearch](https://github.com/karpathy/autoresearch) and battle-tested on the flowdiff project.

**NEVER STOP.** Once the experiment loop has begun, do NOT pause to ask the human if you should continue. The human might be asleep or away and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder. The loop runs until the human interrupts you, period.

---

## How To Use This Template

This is a generic auto-research program. To use it on your project:

1. Copy this folder into your project as `experiments/` or similar
2. Fill in the `[PROJECT CONFIG]` sections below with your project specifics
3. Create `experiments/experiments.jsonl` (empty file)
4. Create `experiments/human-experiment-ideas-for-later.md` for your backlog
5. Run via `ralph.sh --prompt experiments/prompt.md`

---

## [PROJECT CONFIG] — Fill These In

### What You're Optimizing

```
PROJECT_NAME: <your project>
PRIMARY_METRIC: <the metric to optimize, e.g. avg_golden_score, val_bpb, F1, accuracy>
METRIC_DIRECTION: <lower_is_better | higher_is_better>
EVAL_COMMAND: <command to run eval, e.g. "cargo run -- eval --format json 2>/dev/null > /tmp/eval-result.json">
LINT_COMMAND: <optional golden linter, e.g. "cargo run -- lint-goldens --manifest eval/manifest.toml">
TIME_BUDGET_MINUTES: <max minutes per experiment, e.g. 5>
```

### What The Agent Can Change

List the files/parameters that are in-scope for modification:

| Parameter / File | Location | What it controls |
|-----------------|----------|-----------------|
| _example_: `small_group_threshold` | `src/cluster.rs:42` | Max files to consider "small" for merging |
| _example_: ranking weights | `src/rank.rs` | risk/centrality/surface_area/uncertainty |
| ... | ... | ... |

### What The Agent Must NOT Change

- Golden eval files (except when farming new goldens in Phase 1)
- Test fixtures and expected outputs
- The eval harness itself
- This program file

### Eval Corpus Location

```
CORPUS_DIR: <path to eval corpus>
MANIFEST_FILE: <path to eval manifest>
GOLDEN_DIR: <path to golden files>
```

---

## The Three Optimization Modes

The loop cycles through three modes in a fixed schedule:

```
10 MACRO experiments -> 10 MICRO experiments -> 10 GROWING_DATA -> repeat
```

### MACRO (Global Optimization)

Generic approaches that improve quality broadly without target-specific logic. Slower but more durable.

Examples: embeddings, graph algorithms, learned weights, architectural changes, new signal types, community detection, spectral clustering.

These are research projects — may span multiple experiments each.

### MICRO (Local Optimization)

Targeted heuristics addressing specific failure patterns. Quick wins but risk overfitting.

Examples: threshold tuning, config filename lists, extension checks, specific patterns, stop-word lists, weight adjustments.

These are one-shot — test, keep/revert, move on.

**After 3 consecutive MICRO experiments, you MUST do at least 1 MACRO.** This prevents the system from becoming a pile of special cases.

### GROWING_DATA (Corpus Expansion)

Expand the eval corpus to prevent overfitting and validate generalization.

- Add new eval targets with golden labels
- Farm goldens via sub-agents (never scripts — see Golden Rules below)
- Diversify coverage across languages/sizes/patterns

**GATE: After each optimization round (when primary metric plateaus), add at least 30 new eval targets before continuing optimization.** Tuning on a fixed corpus risks overfitting to those exact inputs.

---

## State Machine

The loop has two states:

```
GROWING_DATA -> when corpus < required size for current round
OPTIMISING   -> when corpus >= required size AND all targets have complete golden coverage
```

### Round Progression

| Round | Min Targets | Status |
|-------|------------|--------|
| 1 | (your starting count) | Fill in after baseline |
| 2 | Round 1 + 30 | - |
| 3 | Round 2 + 30 | - |
| N | Round (N-1) + 30 | - |

**If state = GROWING_DATA:** Only Phase 0 (expand corpus) and Phase 1 (golden farming) are allowed. No optimization — that's local overfitting on insufficient data.

**If state = OPTIMISING:** All phases are allowed. Follow the 10/10/10 schedule.

Print state at the start of every experiment:
```
STATE: GROWING_DATA (47 targets, need 60 for Round 2)
STATE: OPTIMISING (63 targets) — MACRO cycle, experiment 4/10
```

---

## The Experiment Loop

Each experiment must complete within the time budget. LOOP FOREVER:

1. **Read state**: Check `experiments/experiments.jsonl` — what's the current best? What hasn't been tried?
2. **Check `experiments/human-experiment-ideas-for-later.md`** for queued ideas from the human.
3. **Pick mode**: Choose the current mode from the 10/10/10 schedule.
4. **Form hypothesis**: Be specific. "Increase threshold from 3 to 5 to reduce singleton explosion on target X" — not "try tweaking parameters".
5. **Make the change**: Edit code, config, or golden file. Keep changes minimal and reversible.
6. **git commit**: Commit the change so you can revert cleanly. Always commit BEFORE running eval.
7. **Run eval**: Execute the eval command. Redirect output to files — do NOT flood your context window.
8. **Read results**: Extract key metrics from the output file. Compare to the last entry in experiments.jsonl.
9. **Record**: Append one JSON line to `experiments/experiments.jsonl` (see format below).
10. **Decide**:
    - If primary metric improved and no target regressed badly: **keep** — advance the branch.
    - If primary metric is equal or worse: **discard** — `git reset --hard HEAD~1`.
    - If any single target regressed more than 10%: **discard** even if average improved.
    - If build crashed or eval errored: **crash** — fix if trivial, otherwise revert and move on.
11. **Loop** — go to step 1.

### Decision Nuances

**Simplicity criterion**: All else being equal, simpler is better. A small improvement (+0.01) that adds ugly complexity is not worth it. Removing code for equal or better results is always a win.

**Crashes**: If a run fails to compile or crashes at runtime, use judgment: if it's a typo or easy fix, fix it and re-run. If the idea is fundamentally broken, log "crash" and move on. Don't spiral on a broken idea for more than 2-3 attempts.

**When you're stuck**: Don't give up — think harder:
- Re-read the code for angles you haven't tried
- Look at the worst-scoring targets — what specific pattern are they failing on?
- Try combining two previous near-miss improvements
- Try more radical architectural changes
- Look at the actual content of failing targets to understand why

---

## The Five Phases

### Phase 0: Expand Corpus (MANDATORY when corpus too small)

Add new eval targets with diverse coverage.

- Prefer reusing existing data (mine more test cases from existing sources)
- Each new target must have complete golden coverage (verified by linter)
- Mix sizes: small (fast iteration), medium (balanced), large (stress test)
- Mix categories: different languages, frameworks, patterns, edge cases
- Create synthetic targets for specific edge cases you want to test

### Phase 1: Farm Goldens via Sub-Agents

Use LLM sub-agents to generate golden eval labels from the actual content.

**CRITICAL: This is NOT an optimization experiment. When the LLM reads content and generates golden labels, this is DATA COLLECTION, not an experiment round. Do not count these as optimization experiments in your experiment history analysis. Do not use golden-farming rounds when analyzing "what improved the metric" — they didn't change the algorithm, they changed the test set.**

For each target without complete golden coverage:

1. Extract the content the sub-agent needs to analyze
2. Spawn a sub-agent (Agent tool) that reads the actual content and generates golden labels
3. **NEVER use scripts or pattern-matching** to classify — goldens are semantic ground truth
4. Review and merge the sub-agent's output
5. Run the linter to verify complete coverage
6. Run eval to see current scores against new goldens

**Sizing strategy for large targets:**
- Small (< 100 items): one sub-agent
- Medium (100-300 items): one sub-agent with the full list
- Large (300+ items): divide-and-conquer — split into chunks of ~50-100, send each chunk to a separate sub-agent in parallel, then merge

### Phase 2: Improve Quality (after golden coverage is complete)

Two sub-tracks — pick based on WHY goldens are failing:

**2a: Parameter Tuning** (system produces output but it's wrong)
- Change ONE constant or weight
- git commit -> run eval -> keep or `git reset --hard HEAD~1`

**2b: Pipeline/Capability Improvements** (system can't detect what it needs to)
- Add new detection patterns, signals, or data sources
- git commit -> run eval -> keep or `git reset --hard HEAD~1`

**How to choose 2a vs 2b:** Look at WHY a golden is failing. If the system sees the data but makes wrong decisions -> 2a (tuning). If the system doesn't see the data at all -> 2b (capability).

### Phase 3: LLM Refinement / Expensive Signals (MEDIUM priority)

Compare providers, models, prompts, and iteration counts. Use caching to avoid duplicate costs.

Track: model, prompt_version, iterations, metric with/without refinement, token count, estimated cost.

### Phase 4: Synthetic Data and Edge Cases (LOW priority)

Create synthetic test cases that exercise specific edge cases not covered by real data.

---

## Golden Rules (The Most Important Section)

These rules are non-negotiable. They were learned through painful experience.

### 1. Never weaken goldens to match current output

Goldens represent the IDEAL output, not what the system currently produces. A golden that the system fails against is a **signal for improvement**, not a bug in the golden. Only modify a golden when the original classification was objectively wrong — and document the reason.

### 2. Never update goldens to make scores look better

If scores drop after adding new goldens, that means the system has blind spots you just discovered. The fix is improving the system, not removing the golden.

### 3. Always use sub-agents for golden generation, never scripts

Goldens are semantic ground truth — they require understanding what each item actually is, not just checking its surface properties (extension, path, name). Pattern-based scripts produce low-quality goldens that miss context.

### 4. Golden constraints are permanent

Once you add a golden that represents genuine domain knowledge, don't remove it. Accumulate goldens over time — they are your most valuable asset.

### 5. Every item must be classified

Partial coverage creates blind spots where the system can silently fail without detection. Enforce 100% coverage with a linter. Phase 2 optimization is BLOCKED until coverage is 100%.

### 6. Goldens are farmed, not tuned

Golden-farming rounds (Phase 1) are data collection, not optimization. When analyzing experiment history to determine "what worked," EXCLUDE golden-farming entries. They changed the test set, not the algorithm.

### 7. Expand corpus before further optimization

After each optimization round plateaus, add 30+ new targets before continuing. This prevents overfitting to the current corpus. Tune on N targets, validate on N+30.

---

## Recording Results

### experiments.jsonl Format

Each experiment gets one JSON line. Every entry MUST include:

```json
{
  "id": 1,
  "timestamp": "2026-03-26T14:30:00Z",
  "hypothesis": "Increase threshold from 3 to 5 to reduce false positives on target X",
  "variable": "src/config.rs:threshold",
  "old_value": "3",
  "new_value": "5",
  "type": "deterministic|golden-generation|llm|corpus-expansion|crash",
  "optimization_mode": "MACRO|MICRO|GROWING_DATA",
  "optimization_scope": "local|global",
  "results": {
    "target_a": {"score": 0.95},
    "target_b": {"score": 0.88}
  },
  "primary_metric": 0.92,
  "primary_metric_prev": 0.90,
  "delta": "+0.02",
  "decision": "keep|discard|crash",
  "notes": "Reduced false positives on X without hurting Y"
}
```

**Type values:**
- `deterministic` — parameter/code tuning (core optimization)
- `golden-generation` — sub-agent generated golden labels (data collection, NOT optimization)
- `llm` — LLM refinement experiments
- `corpus-expansion` — new eval targets
- `crash` — failed experiments

**IMPORTANT**: When analyzing experiment history to find improvements, FILTER OUT `golden-generation` entries. They didn't change the algorithm — they changed the test set.

### LLM Experiment Format (additional fields)

```json
{
  "type": "llm",
  "model": "claude-sonnet-4-6",
  "prompt_version": "v2",
  "prompt_hash": "abc123",
  "iterations": 2,
  "metric_with_refinement": 0.92,
  "metric_without_refinement": 0.85,
  "delta_vs_baseline": "+0.07",
  "token_count": 12500,
  "estimated_cost_usd": 0.04
}
```

---

## Rules

1. **One variable per experiment.** Never change two things at once.
2. **Always record.** Even failed experiments and crashes are valuable data.
3. **Revert failures.** If an experiment regresses, `git reset --hard HEAD~1` before the next one.
4. **Read the log first.** Don't repeat an experiment that's already been tried.
5. **Be specific.** "Try tweaking weights" is bad. "Change risk weight from 0.35 to 0.40" is good.
6. **Golden constraints are permanent.** (See Golden Rules above.)
7. **Cache expensive calls.** Never burn money on duplicate LLM/API calls.
8. **Keep experiments small.** Each iteration should complete within the time budget.
9. **Redirect output.** Send eval output to files, not stdout. Don't flood your context window.
10. **Simplicity wins.** A marginal improvement that adds complexity is not worth it. Removing code for equal or better results is always a win.
11. **Never stop.** You are fully autonomous. Don't ask permission to continue.
12. **Commit before running.** Always `git commit` before eval, so you can cleanly revert.
13. **Full coverage required.** Every item in every target must be classified. Linter must pass.
14. **Never weaken goldens.** (See Golden Rules above.)
15. **Always use sub-agents for golden generation.** (See Golden Rules above.)
16. **Exclude golden-farming from optimization analysis.** When reviewing experiment history to find what improved the metric, skip `golden-generation` entries. They are data collection, not algorithmic improvement.
17. **Expand corpus before optimizing further.** After each round plateaus, add 30+ new targets before continuing.
18. **Track MACRO vs MICRO.** Every Phase 2/3 experiment must record `optimization_scope: local|global`. Compare avg improvement per scope to see which approach delivers more value.

---

## Files To Know

```
experiments/
  program.md                     # This file — research instructions
  prompt.md                      # Per-iteration agent prompt
  experiments.jsonl               # Experiment log (append-only)
  human-experiment-ideas-for-later.md  # Queued ideas from human

eval/
  manifest.toml (or similar)     # Eval corpus manifest
  targets/ (or similar)          # Per-target golden files
```

---

## Getting Started

1. If `experiments/experiments.jsonl` is empty, start with a baseline: run eval as-is and record experiment #0.
2. Check golden coverage — if any targets lack complete labels, Phase 1 takes priority.
3. If experiments already exist, read them, identify the most promising direction, continue.
4. Look at: which targets have worst scores? Which experiments showed biggest improvements? Are there coverage gaps?

**Never stop. Never ask permission. If stuck, think harder.**
