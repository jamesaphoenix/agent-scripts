# JUD continuation, 18 October 2026

Finite Mac Studio continuation of the existing run, explicitly requested by James. Runtime source is pinned to the recorded project branch and SHA in the run takeover record. The controller writes CONTINUATION-STATUS.json and stage logs in the existing run directory. The first three pairs require an exact-hash inspection receipt before the remaining 48. No automatic restart or paid retry. The process keeps the one-read ElevenLabs key in memory only.

Install on Studio through scripts/install-launchd-tasks.sh --task jud-video-continuation-20261018. Do not install on MacBook. Preserve the original 22:00 UTC deadline and GBP50 run-2 ledger. Use the cached service token through lib/op-service-account-env.sh; never source shell profiles. The run records a bounded credential cooldown before its one quota observation and secret read.
