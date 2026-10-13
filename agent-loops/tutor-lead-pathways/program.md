# Discovery experiments

Use the small experiment loop in [autoresearch](https://github.com/karpathy/autoresearch): establish a baseline, make one bounded change, evaluate it, and keep or discard it. Prefer removing complexity when results are equal.

## Fixed constraints

The prepared run's identity, start, discovery cutoff, final deadline, plan SHA256, paid ledger, caps, crawler limits and monitor interval are fixed. Resume never creates a new window. Experiments must not submit a cached paid request again or spend outside the existing budget. No emails, purchases, messages, forms or tenant imports are authorised by this loop.

Tutor counts must retain the existing county, subject, source, franchise and verification rules. Search results, platform profiles, service-area claims and page fetches do not establish independent ownership, current activity or outreach eligibility. Do not change the evaluator, labels or qualification rules to improve a score.

## Choose an experiment

Write one falsifiable hypothesis and name the single policy being changed. Record the baseline commit and the allowed source files before editing. Keep unrelated work intact. Do not reset a shared branch to discard an experiment; revert only its own changes.

Freeze the inputs and judgement before running either variant. Store private contact/page data under the run's `review/autoresearch/`, with SHA256 hashes and source attribution. The evaluator stays unchanged for the pair. Do not install new dependencies.

For seed admission, use `evaluate-seeds.py` against frozen URLs with observed fetch outcomes. Minimise `admissionErrors`, while preserving every labelled HTML page. This is a queue-efficiency check, not evidence of additional tutors or higher live conversion.

For live source comparisons, use equal town sets, query/attempt allowances and a fixed fifteen-minute observation window. Measure distinct research candidates with attributed target-area and 11+/primary evidence; separately report unknown locations, duplicates, held sources, failures, elapsed time and provider receipt cost. Keep live observations separate from offline replay. A historical search is not a controlled live baseline.

## Run and judge

1. Save the baseline output before editing. Offline replay has a sixty-second process ceiling and the same frozen input for both variants. Build time is separate.
2. Make the one policy change and run the relevant regression checks.
3. Evaluate the candidate with the original evaluator and input hashes. Missing, duplicate or unknown decisions are failures, not zero-error results.
4. Append `results.tsv`: `experiment`, `commit`, `fixture_sha256`, `metric`, `value`, `guardrail_failures`, `elapsed_seconds`, `paid_usd`, `status`, `description`, `evidence`. Use `baseline`, `keep`, `discard` or `crash`. Never overwrite earlier rows.
5. Keep an improvement only when the fixed metric improves and the guardrails pass. Keep an equally good simplification only with a smaller diff. Otherwise discard it. Record uncertainty when a live comparison is too small or confounded.
6. Deploy a kept change only after checks pass, at a safe boundary between children and settled paid calls. Record the running source/binary hash. Never terminate an unrelated process or extend the run to accommodate an experiment.
7. Continue within the authorised window. The fifteen-minute monitor must remain independent of experiments. Finish with the ledger, exports and completion audit, even when some ideas remain untested.

Start with saved error evidence. Small admission, query-selection, ranking or extraction changes are preferable to infrastructure rewrites. Further ideas may use directory profile searches, local community publications and first-party link graphs. Test each separately. More rows alone are not an improvement.
