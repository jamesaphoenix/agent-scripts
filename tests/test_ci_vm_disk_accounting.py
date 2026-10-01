import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'agent-loops/ci-vm-cleanup/cleanup-ci-vm.sh'


class CiVmDiskAccountingTests(unittest.TestCase):
    def run_janitor(self, profile, directory):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disk = root / '_lima/_disks' / directory / 'datadisk'
            disk.parent.mkdir(parents=True)
            disk.write_bytes(b'fixture disk allocation' * 200)
            log = root / 'calls.jsonl'
            for name in ('docker', 'colima'):
                cli = root / name
                cli.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['CI_ACCOUNTING_CALLS'], 'a') as log:
    log.write(json.dumps({'cli': Path(sys.argv[0]).name, 'args': args}) + '\\n')
if '--help' in args:
    print('--max-used-space')
elif 'fstrim' in args:
    print('/var/lib/docker: 1 MiB potentially trimmed')
elif 'df' in args:
    print('fixture filesystem usage')
''')
                cli.chmod(0o755)
            env = dict(os.environ)
            env.update(PATH=str(root) + os.pathsep + env['PATH'], COLIMA_HOME=str(root),
                       CI_VM_CLEANUP_PROFILE=profile, CI_VM_CLEANUP_LOCK_DIR=str(root / 'lock'),
                       CI_ACCOUNTING_CALLS=str(log), CI_VM_CLEANUP_PRUNE_ANONYMOUS_VOLUMES='0',
                       CI_VM_CLEANUP_DISABLE_FILE='', DRY_RUN='0')
            result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True,
                                    text=True, timeout=30)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('host datadisk before:', result.stdout)
            self.assertIn('host datadisk after:', result.stdout)
            self.assertTrue(any('fstrim' in call['args'] for call in calls))
            self.assertFalse(any('volume' in call['args'] for call in calls))

    def test_named_profile_uses_prefixed_lima_disk(self):
        self.run_janitor('ci', 'colima-ci')

    def test_default_profile_uses_colima_disk(self):
        self.run_janitor('default', 'colima')

    def test_unprefixed_layout_remains_supported(self):
        self.run_janitor('ci', 'ci')


if __name__ == '__main__':
    unittest.main()
