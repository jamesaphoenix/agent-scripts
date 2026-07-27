#!/usr/bin/env bash
# Log-only wrapper: reports what the reaper WOULD kill without killing anything.
# Flip the runners to enforcing by pointing ACTIONS_RUNNER_HOOK_JOB_COMPLETED at
# job-completed.sh instead of this file. Always exits 0 so it can never fail a job.
exec "$(dirname "${BASH_SOURCE[0]}")/job-completed.sh" --dry-run
