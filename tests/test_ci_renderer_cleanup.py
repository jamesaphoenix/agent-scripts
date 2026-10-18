import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'agent-loops/ci-vm-cleanup/cleanup-ci-vm.sh'


class CiRendererCleanupTests(unittest.TestCase):
    def run_cleanup(self, dry_run=False, inventory_failure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'calls.jsonl'
            cli = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
context = os.environ.get('DOCKER_CONTEXT', 'desktop-linux')
with open(os.environ['RENDERER_TEST_CALLS'], 'a') as output:
    output.write(json.dumps({'cli': name, 'args': args, 'context': context}) + '\\n')
if '--help' in args:
    print('--max-used-space')
    sys.exit(0)
if name == 'colima':
    if 'df' in args: print('fixture filesystem usage')
    sys.exit(0)
if args[:2] == ['image', 'ls']:
    for tag in ('old', 'recent', 'stopped', 'running'):
        print('octospark-renderer\\t' + tag + '\\t<none>\\tsha256:' + tag)
    print('octospark-media-runtime\\told\\t<none>\\tsha256:runtime')
    print('postgres\\told\\t<none>\\tsha256:postgres')
elif args[:2] == ['image', 'inspect']:
    tag = args[2].split(':')[-1]
    if '--format' in args: print('sha256:' + tag)
    else:
        stamp = os.environ['RENDERER_TEST_RECENT' if tag == 'recent' else 'RENDERER_TEST_OLD']
        print(json.dumps([{'Created': stamp, 'Metadata': {'LastTagTime': stamp}}]))
elif args[:1] == ['ps'] and context == 'colima-ci':
    if os.environ.get('RENDERER_TEST_INVENTORY_FAILURE'): sys.exit(1)
    print('stopped-container\\nrunning-container')
elif args[:1] == ['inspect']:
    tag = args[1].split('-')[0]
    print('octospark-renderer:' + tag if args[-1] == '{{.Config.Image}}' else 'sha256:' + tag)
elif '--help' in args: print('--max-used-space')
'''
            for name in ('docker', 'colima'):
                binary = root / name
                binary.write_text('#!' + sys.executable + '\n' + cli)
                binary.chmod(0o755)
            env = dict(os.environ)
            env.update(PATH=str(root) + os.pathsep + env['PATH'],
                       RENDERER_TEST_CALLS=str(log), RENDERER_TEST_OLD='2020-01-01T00:00:00Z',
                       RENDERER_TEST_RECENT=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       CI_VM_CLEANUP_PROFILE='ci', CI_VM_CLEANUP_DISABLE_FILE='',
                       CI_VM_CLEANUP_LOCK_DIR=str(root / 'ci-lock'),
                       DOCKER_CLEANUP_LOCK_DIR=str(root / 'image-lock'),
                       DOCKER_CLEANUP_DISABLE_FILE='', DOCKER_CONTEXT='desktop-linux',
                       DOCKER_CLEANUP_NATIVE_PIN_FILES=str(root / 'host-pins-not-in-ci.json'),
                       CI_VM_CLEANUP_PRUNE_ANONYMOUS_VOLUMES='0',
                       DRY_RUN='1' if dry_run else '0',
                       RENDERER_TEST_INVENTORY_FAILURE='1' if inventory_failure else '')
            result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True,
                                    text=True, timeout=30)
            return result, [json.loads(line) for line in log.read_text().splitlines()]

    def test_removes_only_old_unreferenced_renderer_and_runtime_before_trim(self):
        result, calls = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stderr)
        removed = [c for c in calls if c['args'][:2] == ['image', 'rm']]
        self.assertEqual({c['args'][2] for c in removed},
                         {'octospark-renderer:old', 'octospark-media-runtime:old'})
        self.assertTrue(all(c['context'] == 'colima-ci' for c in removed))
        self.assertFalse(any('volume' in c['args'] or c['args'][:2] == ['container', 'prune'] for c in calls))
        trim = next(i for i, c in enumerate(calls) if 'fstrim' in c['args'])
        self.assertTrue(all(calls.index(c) < trim for c in removed))

    def test_dry_run_never_mutates_either_engine_or_trims(self):
        result, calls = self.run_cleanup(dry_run=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('rm' in c['args'] or 'fstrim' in c['args'] or
                             ('prune' in c['args'] and '--help' not in c['args']) for c in calls))

    def test_failed_inventory_aborts_before_image_removal(self):
        result, calls = self.run_cleanup(inventory_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c['args'][:2] == ['image', 'rm'] for c in calls))


if __name__ == '__main__':
    unittest.main()
