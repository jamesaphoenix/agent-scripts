# Tutor discovery health checks

User-authorised 10 October 2026: check the existing county-wide crawl every thirty minutes for eight hours. Install on Mac Studio through `scripts/install-launchd-tasks.sh --task tracelearn-tutor-watch-20261010`.

The finite monitor starts immediately, stores its original eight-hour deadline and unloads itself after its final check. Reports live in `state/tracelearn-tutor-watch-20261010/reports/`; `latest.json` records progress, budget exposure, query outcomes, candidate totals, holds and recovery requests. It uses no credentials or paid API calls.

Health requires checkpoint progress as well as process ownership. Valid cooldowns and recorded provider, budget, page and site limits are preserved. Input digests, run identity, original discovery deadline and budget are verified before recovery. An active or uncertain paid request blocks interruption. A stalled, exactly identified crawler may receive SIGTERM and a launchd resume request for its original checkpoint. No force-kill, provider replay, changed budget or extended discovery window is allowed. At most three recovery requests are made, and subsequent reports must verify recovery.

This does not assert that every tutor has been found or approve any candidate for outreach. Mac Studio must remain powered and awake; launchd resumes checks after a host interruption, within the saved monitoring window.
