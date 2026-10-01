import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'agent-loops/docker-cleanup/cleanup-local-docker.sh'


class HostDockerContextTests(unittest.TestCase):
    def run_janitor(self, override=None, image=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / 'docker'
            docker.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
args = sys.argv[1:]
context = os.environ.get('DOCKER_CONTEXT', 'desktop-linux')
with open(os.environ['CONTEXT_TEST_CALLS'], 'a') as output:
    output.write(json.dumps({'args': args, 'context': context}) + '\\n')
if args[:2] == ['buildx', 'prune']:
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
    print(os.environ['CONTEXT_TEST_IMAGE'])
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
            if image is not None:
                env['CONTEXT_TEST_IMAGE'] = json.dumps([image])
            else:
                env.pop('CONTEXT_TEST_IMAGE', None)
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

if __name__ == '__main__':
    unittest.main()
