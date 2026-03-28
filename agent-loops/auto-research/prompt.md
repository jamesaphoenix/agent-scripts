# Auto-Research Loop — One Iteration

Run the autonomous research loop. Each invocation runs ONE experiment. **Do NOT ask if you should continue** — you are fully autonomous.

## Step 1: Read Context

Read these files to understand current state:

1. `experiments/program.md` — full research instructions, rules, golden rules, phases
2. `experiments/experiments.jsonl` — experiment history (what's been tried, what worked)
3. `experiments/human-experiment-ideas-for-later.md` — queued ideas from the human (check for new additions)

Also read your project's CLAUDE.md/AGENTS.md for project-specific conventions.

## Step 2: Setup (first run only)

If this is the first run of a session:

1. Create branch: `git checkout -b autoresearch/<tag>` (e.g. `autoresearch/mar28`)
2. Run baseline eval and record as experiment #0 if no baseline exists

## Step 3: Determine State

Check corpus size and golden coverage:

```bash
# Count eval targets (adjust path to your project)
TARGET_COUNT=$(ls eval/targets/*.toml 2>/dev/null | wc -l)
echo "Targets: $TARGET_COUNT"

# Check golden coverage (adjust to your linter command)
# <your-lint-command>
```

Determine state:
- **GROWING_DATA** — if corpus < required size for current optimization round
- **OPTIMISING** — if corpus >= required size AND all targets have 100% golden coverage

Print state: `STATE: <state> (<N> targets)`

## Step 4: Determine Mode

Check the experiment count within each mode cycle to know where you are in the 10/10/10 rotation:

```
10 MACRO -> 10 MICRO -> 10 GROWING_DATA -> repeat
```

Read `experiments/experiments.jsonl` and count recent entries by `optimization_mode` to determine which mode is current.

**If state = GROWING_DATA:** Only Phase 0 + Phase 1 allowed (corpus expansion + golden farming). Skip to Step 5.

**If state = OPTIMISING:** Follow the 10/10/10 schedule.

## Step 5: Pick Phase and Form Hypothesis

Based on mode and state, pick the highest-priority phase with unfinished work:

| Priority | Phase | When |
|----------|-------|------|
| HIGHEST | Phase 0: Expand corpus | Corpus below threshold |
| HIGHEST | Phase 1: Farm goldens | Golden linter reports gaps |
| HIGH | Phase 2: Improve quality | Goldens complete, in OPTIMISING |
| MEDIUM | Phase 3: LLM refinement | After Phase 2 plateaus |
| LOW | Phase 4: Synthetic data | Interleave with other phases |

Form a specific hypothesis. "Increase X from 3 to 5 to reduce Y on target Z" — not "try tweaking parameters."

**IMPORTANT: When analyzing experiment history to decide what to try next, EXCLUDE experiments with type `golden-generation`.** Those were data collection rounds (expanding the test set), not algorithmic improvements. They tell you nothing about what code changes work. Only analyze `deterministic`, `llm`, and `crash` entries to understand optimization trends.

## Step 6: Execute

1. **Make the change** — keep it minimal and reversible
2. **git commit** — always commit BEFORE running eval
3. **Run eval** — redirect output to a file:
   ```bash
   # Adjust to your eval command
   <your-eval-command> > /tmp/eval-result.json
   ```
4. **Read results** — extract key metrics from the output file

## Step 7: Record

Append one JSON line to `experiments/experiments.jsonl`:

```json
{
  "id": <next_id>,
  "timestamp": "<ISO 8601>",
  "hypothesis": "<specific hypothesis>",
  "variable": "<what you changed>",
  "old_value": "<before>",
  "new_value": "<after>",
  "type": "<deterministic|golden-generation|llm|corpus-expansion|crash>",
  "optimization_mode": "<MACRO|MICRO|GROWING_DATA>",
  "optimization_scope": "<local|global>",
  "primary_metric": <number>,
  "primary_metric_prev": <number>,
  "delta": "<+/-0.XX>",
  "decision": "<keep|discard|crash>",
  "notes": "<what happened and why>"
}
```

## Step 8: Keep or Revert

- **Improved + no target regressed badly** -> keep, advance branch
- **Equal or worse** -> `git reset --hard HEAD~1`
- **Any single target regressed > 10%** -> discard even if average improved
- **Crashed** -> fix if trivial, otherwise revert and move on

## Reminders

- **One variable per experiment.** Never change two things at once.
- **Goldens are sacred.** Never weaken goldens to match current output. Never update goldens to make scores look better. (See Golden Rules in program.md.)
- **Exclude golden-farming from trend analysis.** When figuring out "what's working," ignore `golden-generation` entries — they changed the test set, not the algorithm.
- **Simplicity wins.** Marginal improvement + added complexity = not worth it.
- **Redirect output.** Always pipe eval results to files, never stdout.
- **Never stop.** You are fully autonomous. If stuck, think harder.
