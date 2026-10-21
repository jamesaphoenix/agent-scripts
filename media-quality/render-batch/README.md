# Smart Cub standalone render batch

Runs the frozen 21 October CTA revision on Mac Studio without Codex, Herdr,
provider credentials or paid requests. Existing hash-checked render checkpoints
are resumed. Marketing rendering, teaching joins and native text scans overlap;
the join audio verifier runs after all teaching outputs exist.

The registered launchd task owns the process lifetime. SSH and the MacBook may
disconnect. On reboot it resumes; a controller crash reaps its own recorded
workers before restarting. A failed rendering or verification step is recorded
and stops without retrying a provider or claiming approval. Independent rendering
continues if another producer fails. Frozen helper/source drift stops execution.

The `rendered-videos/` folder contains all 586 selected candidates after production
and technical checks finish. Existing approved media is reused only for exact
unchanged outputs. The previous delivery pointer stays unchanged. The candidate
manifest preserves distribution restrictions and marks editorial review pending.
Uncommitted partial outputs are quarantined; completed media and evidence remain.

```sh
python3 media-quality/render-batch/runner.py configure --workflow WORKFLOW --runtime WORKFLOW/standalone-runner
scripts/install-launchd-tasks.sh --task smartcub-render-20261021
python3 media-quality/render-batch/runner.py status --config WORKFLOW/standalone-runner/config.json
```

Logs and status are in `WORKFLOW/standalone-runner/`. Resume a deliberately stopped
or failed batch with `launchctl kickstart gui/$(id -u)/com.jud.smartcub-render-20261021`
after correcting its recorded failure. A running instance holds an exclusive lock.

Run focused checks with `python3 -m unittest discover -s tests -p test_smartcub_render_runner.py`.
