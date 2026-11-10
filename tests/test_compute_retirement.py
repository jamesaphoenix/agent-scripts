import json
from pathlib import Path
import unittest
from unittest.mock import patch


class ComputeRetirementTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'agent-loops/octospark-compute/watch.py'
        self.scope = {'__name__': 'retirement_test'}
        exec(compile(path.read_text().split('\nos.umask(0o077)')[0], str(path), 'exec'), self.scope)
        self.rows = {
            'desktop-linux': [self.container('desktop-old', 'octospark-prod-live', 'worker', False)],
            'colima-ci': [self.container('colima-old', 'octospark-staging-live', 'worker', False), self.container('colima-otel', 'octospark-staging-live', 'otel-collector', True), self.container('ci-worker', 'octospark-ci', 'worker', True), self.container('ci-grafana', 'trace-learn-ci', 'grafana', True)]
        }

    def container(self, identifier, project, service, running):
        return {'Id': identifier, 'Name': '/' + identifier, 'Config': {'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': service}}, 'State': {'Running': running}}

    def output(self, args, **kwargs):
        rows = self.rows[args[2]]
        return ' '.join(c['Id'] for c in rows) if 'ps' in args else json.dumps(rows)

    def test_cleanup_includes_legacy_colima_stack_and_preserves_ci(self):
        with patch('subprocess.check_output', side_effect=self.output), patch('subprocess.run') as run:
            self.scope['cleanup_retired']()
        removed = {(call.args[0][2], call.args[0][-1]) for call in run.call_args_list}
        self.assertEqual(removed, {('desktop-linux', 'desktop-old'), ('colima-ci', 'colima-old'), ('colima-ci', 'colima-otel')})

    def test_running_legacy_colima_worker_blocks_stable_health(self):
        self.rows['colima-ci'][0]['State']['Running'] = True
        with patch('subprocess.check_output', side_effect=self.output):
            with self.assertRaisesRegex(RuntimeError, 'Legacy Studio application'):
                self.scope['assert_primary_retired']()

    def test_running_ci_workers_do_not_block_retirement(self):
        with patch('subprocess.check_output', side_effect=self.output):
            self.scope['assert_primary_retired']()

    def test_render_staging_is_not_probed_before_its_release_exists(self):
        import tempfile
        probed = []

        class Reply:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{}'

        def opened(url, timeout=0):
            probed.append(url)
            return Reply()

        with tempfile.TemporaryDirectory() as tmp:
            self.scope['STATE'] = Path(tmp)
            with patch('urllib.request.urlopen', side_effect=opened):
                self.assertNotIn('render-staging', self.scope['native_health']())
                self.assertFalse(any(':8098' in url for url in probed))
                (Path(tmp) / 'current-render-staging').mkdir()
                self.assertIn('render-staging', self.scope['native_health']())
                self.assertTrue(any(':8098' in url for url in probed))


class RenderStagingRunnerTest(unittest.TestCase):
    def run_role(self, role, create_env=False, create_link=False):
        import subprocess, sys, tempfile
        runner = Path(__file__).resolve().parents[1] / 'agent-loops/octospark-compute/run.py'
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'config.json').write_text('{"python": "/usr/bin/python3"}')
            (state / 'env').mkdir()
            if create_env:
                (state / 'env' / (role + '.env')).write_text('RENDER_HOST_PORT=8098\n')
            if create_link:
                (state / ('current-' + role)).mkdir()
            return subprocess.run([sys.executable, str(runner), role, str(state)], capture_output=True, text=True, timeout=20)

    def test_render_staging_exits_cleanly_until_provisioned(self):
        for env, link in ((False, False), (True, False), (False, True)):
            with self.subTest(env=env, link=link):
                result = self.run_role('render-staging', env, link)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('not provisioned', result.stdout)

    def test_production_render_still_fails_loudly_when_unprovisioned(self):
        self.assertNotEqual(self.run_role('render').returncode, 0)


if __name__ == '__main__':
    unittest.main()
