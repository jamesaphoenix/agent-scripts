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


if __name__ == '__main__':
    unittest.main()
