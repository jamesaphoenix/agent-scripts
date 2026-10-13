import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class ProcessDeadlineTests(unittest.TestCase):
    def test_timeout_reaps_wedged_descendant_after_parent_exits_on_term(self):
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / 'child.pid'
            code = "import os,signal,time,sys;pid=os.fork();open(sys.argv[1],'w').write(str(pid)) if pid else None;signal.signal(signal.SIGTERM,signal.SIG_IGN) if not pid else None;time.sleep(30)"
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('bounded-process.py')), '--timeout', '1', '--grace', '.2', '--', sys.executable, '-c', code, str(pid_file)], timeout=5)
            self.assertEqual(result.returncode, 124)
            pid = int(pid_file.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
                if not state or state.startswith('Z'):
                    break
                time.sleep(.05)
            else:
                os.kill(pid, signal.SIGKILL)
                self.fail('health-check descendant survived the deadline')

    def test_normal_exit_preserves_status(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('bounded-process.py')), '--timeout', '2', '--', sys.executable, '-c', 'raise SystemExit(7)'])
        self.assertEqual(result.returncode, 7)
