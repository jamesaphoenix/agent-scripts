import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'agent-loops/docker-cleanup/cleanup-local-docker.sh'


class HostDockerContextTests(unittest.TestCase):
    def run_janitor(self, override=None, image=None, image_after=None, inventory_failure=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / 'docker'
            docker.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
args = sys.argv[1:]
context = os.environ.get('DOCKER_CONTEXT', 'desktop-linux')
with open(os.environ['CONTEXT_TEST_CALLS'], 'a') as output:
    output.write(json.dumps({'args': args, 'context': context}) + '\\n')
if args and args[0] == 'ps' and os.environ.get('CONTEXT_TEST_INVENTORY_FAILURE'):
    if os.environ['CONTEXT_TEST_INVENTORY_FAILURE'] == 'list':
        sys.exit(1)
    print('fixture-container')
elif args and args[0] == 'inspect' and os.environ.get('CONTEXT_TEST_INVENTORY_FAILURE'):
    sys.exit(1)
elif args[:2] == ['buildx', 'prune']:
    if '--help' in args:
        print('--max-used-space')
    else:
        builder = args[args.index('--builder') + 1]
        if builder != context:
            print('ERROR: use docker --context=' + builder + ' buildx', file=sys.stderr)
            sys.exit(1)
        print('Total: 0B')
elif args[:2] == ['image', 'ls'] and os.environ.get('CONTEXT_TEST_IMAGE'):
    print('registry/trace-learn/api\\tcandidate\\tsha256:fixture')
elif args[:2] == ['image', 'inspect'] and os.environ.get('CONTEXT_TEST_IMAGE'):
    with open(os.environ['CONTEXT_TEST_CALLS']) as calls:
        inspected = sum(json.loads(line)['args'][:2] == ['image', 'inspect'] for line in calls)
    key = 'CONTEXT_TEST_IMAGE_AFTER' if inspected > 1 and os.environ.get('CONTEXT_TEST_IMAGE_AFTER') else 'CONTEXT_TEST_IMAGE'
    print(os.environ[key])
elif args and args[0] in ('info', 'ps', 'images', 'image', 'network', 'container'):
    pass
else:
    print('Unexpected Docker call: ' + repr(args), file=sys.stderr)
    sys.exit(2)
''')
            docker.chmod(0o755)
            log = root / 'calls.jsonl'
            env = dict(os.environ)
            env.update(PATH=str(root) + os.pathsep + env['PATH'],
                       CONTEXT_TEST_CALLS=str(log), KEEP_DEPLOY_ARTIFACTS='0',
                       DOCKER_CLEANUP_LOCK_DIR=str(root / 'lock'),
                       DOCKER_CONTEXT='desktop-linux')
            for key in ('DOCKER_CLEANUP_CONTEXT', 'DOCKER_CLEANUP_BUILDER_NAME', 'DRY_RUN'):
                env.pop(key, None)
            env.pop('CONTEXT_TEST_INVENTORY_FAILURE', None)
            if inventory_failure:
                env['CONTEXT_TEST_INVENTORY_FAILURE'] = inventory_failure
            if image is not None:
                env['CONTEXT_TEST_IMAGE'] = json.dumps([image])
            else:
                env.pop('CONTEXT_TEST_IMAGE', None)
            if image_after is not None:
                env['CONTEXT_TEST_IMAGE_AFTER'] = json.dumps([image_after])
            else:
                env.pop('CONTEXT_TEST_IMAGE_AFTER', None)
            if override:
                env['DOCKER_CLEANUP_CONTEXT'] = override
            result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_saved_context_drift_does_not_break_host_pruning(self):
        result, calls = self.run_janitor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(calls)
        self.assertEqual({call['context'] for call in calls}, {'default'})
        prune = next(call['args'] for call in calls if call['args'][:2] == ['buildx', 'prune'] and '--help' not in call['args'])
        self.assertEqual(prune[prune.index('--builder') + 1], 'default')

    def test_failed_container_inventory_aborts_before_mutation(self):
        for failure in ('list', 'inspect'):
            with self.subTest(failure=failure):
                result, calls = self.run_janitor(inventory_failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('refusing Docker cleanup', result.stdout)
                self.assertFalse(any(call['args'][:2] in (
                    ['image', 'rm'], ['image', 'prune'], ['network', 'prune'],
                    ['buildx', 'prune']) for call in calls))

    def test_explicit_context_uses_its_matching_builder(self):
        result, calls = self.run_janitor('host-maintenance')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({call['context'] for call in calls}, {'host-maintenance'})
        prune = next(call['args'] for call in calls if call['args'][:2] == ['buildx', 'prune'] and '--help' not in call['args'])
        self.assertEqual(prune[prune.index('--builder') + 1], 'host-maintenance')


    def test_old_build_pulled_recently_is_retained(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        image = {'Created': (now - datetime.timedelta(days=365)).isoformat(),
                 'Metadata': {'LastTagTime': (now - datetime.timedelta(hours=2)).isoformat()}}
        result, calls = self.run_janitor(image=image)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call['args'][:2] == ['image', 'rm'] for call in calls))

    def test_both_old_timestamps_allow_unprotected_release_removal(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        old = (now - datetime.timedelta(days=30)).isoformat()
        result, calls = self.run_janitor(image={'Created': old, 'Metadata': {'LastTagTime': old}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call['args'] == ['image', 'rm', 'registry/trace-learn/api:candidate'] for call in calls))

    def test_missing_local_tag_timestamp_retains_release(self):
        result, calls = self.run_janitor(image={'Created': '2020-01-01T00:00:00Z'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call['args'][:2] == ['image', 'rm'] for call in calls))

    def test_retag_after_discovery_is_retained_at_mutation_time(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        old = (now - datetime.timedelta(days=30)).isoformat()
        initial = {'Created': old, 'Metadata': {'LastTagTime': old}}
        reused = {'Created': old, 'Metadata': {'LastTagTime': now.isoformat()}}
        result, calls = self.run_janitor(image=initial, image_after=reused)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(sum(call['args'][:2] == ['image', 'inspect'] for call in calls), 2)
        self.assertFalse(any(call['args'][:2] == ['image', 'rm'] for call in calls))

    def test_os_python_can_age_nanosecond_docker_timestamps(self):
        interpreter = Path('/usr/bin/python3')
        if not interpreter.exists():
            self.skipTest('OS Python is unavailable')
        image = {'Created': '2020-01-01T00:00:00.12345Z',
                 'Metadata': {'LastTagTime': '2020-01-02T00:00:00.987654321Z'}}
        helper = SCRIPT.parent / 'lib/image-age.py'
        result = subprocess.run([str(interpreter), str(helper)], input=json.dumps([image]),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == '__main__':
    unittest.main()
