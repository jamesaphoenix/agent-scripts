# Finite Trace Learn tutor discovery

One seven-hour research run for independent primary and 11+ tutors in Buckinghamshire, including Milton Keynes. The Go runner retains an absolute deadline and a USD100 cap across restarts. This job sends no email and purchases no domains.

Author here on the MacBook. Deploy this directory and the reviewed `tutor-crawler` binary to the Mac Studio. Register/install only through `scripts/install-launchd-tasks.sh --task tracelearn-tutor-discovery-20261009`. Do not run the installer before UI and runner verification finish.

Credentials are resolved through 1Password at runtime. The wrapper sources the shared profile and cached service-account helper. The registry explicitly permits profile-or-keychain authentication: installation verifies the exported service account without trying to write the login keychain over SSH. The launchd plist, job metadata and logs contain no credentials. Only DataForSEO references are loaded. The wrapper disables desktop-app settings, biometric integration and caching for this service-account-only job. This avoids the macOS daemon settings probe reported in the [1Password issue](https://github.com/1Password/shell-plugins/issues/606) and [follow-up report](https://www.1password.community/developers-69/op-cli-triggers-macos-access-data-from-other-apps-prompt-on-every-run-in-daemon-contexts-root-cause-unconditional-policies-json-probe-even-with-a-service-account-token-25335). Credential preflight has a thirty-second timeout.

`state/tracelearn-tutors-20261009/job.json` records the immutable window. `discovery.json` is the authoritative runner ledger and checkpoint; stdout is only progress. An uncertain paid request retains its reservation and is never blindly retried. Keep this state to resume the same run. Never delete it to recover a failed request.

Launchd restarts failures with a two-minute throttle. Successful completion stops the job. A reboot resumes the original run and deadline. Keep the permanent OS flock inode: process exit releases ownership automatically. A loaded process alone does not establish successful discovery or delivery.

Results are research artifacts for authenticated tenant-scoped import into the outbound dashboard. Research completion and contact import are separate proof steps. Use the project CLI or the dashboard CSV import under the intended organisation, rather than copying another organisation's database or impersonating its administrator.

Status: `launchctl print gui/$(id -u)/com.jud.tracelearn-tutor-discovery-20261009` and `tutor-crawler discover-status --state state/tracelearn-tutors-20261009/discovery.json` from the runtime agent-scripts directory. Stop with `launchctl bootout gui/$(id -u)/com.jud.tracelearn-tutor-discovery-20261009`; the checkpoint remains resumable.
