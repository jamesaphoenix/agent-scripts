import importlib.util
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('admission', Path(__file__).with_name('review_admission.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def intent(index=1):
    return {'id': f'video-{index}', 'aspect': 'tall', 'outputSha256': f'{index:064x}'}


class AdmissionTests(unittest.TestCase):
    def test_family_reservations_and_total_four(self):
        with tempfile.TemporaryDirectory() as directory:
            from contextlib import ExitStack
            with ExitStack() as stack:
                for index, family in enumerate(['teaching', 'teaching', 'marketing', 'corrected']):
                    stack.enter_context(module.admission(directory, family=family, intent=intent(index), timeout=0))
                for family in module.FAMILY_SLOTS:
                    with self.assertRaises(TimeoutError):
                        with module.admission(directory, family=family, intent=intent(99), timeout=0):
                            self.fail('Fifth admission')

    def test_same_exact_intent_cannot_overlap_across_families(self):
        with tempfile.TemporaryDirectory() as directory:
            with module.admission(directory, family='teaching', intent=intent(), timeout=0):
                with self.assertRaises(TimeoutError):
                    with module.admission(directory, family='corrected', intent=intent(), timeout=0):
                        self.fail('Duplicate exact media admitted')
            with module.admission(directory, family='corrected', intent=intent(), timeout=0):
                pass

    def test_stopped_wait_or_stopped_child_never_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = Path(directory) / 'STOP'
            stop.touch()
            with self.assertRaises(module.AdmissionStopped):
                with module.admission(directory, family='marketing', intent=intent(), stop_path=stop):
                    self.fail('Stopped admission')
            stop.unlink()
            with module.admission(directory, family='marketing', intent=intent(), stop_path=stop) as lease:
                stop.touch()
                with self.assertRaises(module.AdmissionStopped):
                    lease.popen([sys.executable, '-c', 'raise AssertionError("must not run")'])

    def test_child_keeps_slot_after_dispatcher_context_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            with module.admission(directory, family='marketing', intent=intent(), timeout=0) as lease:
                child = lease.popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
            try:
                with self.assertRaises(TimeoutError):
                    with module.admission(directory, family='marketing', intent=intent(2), timeout=0):
                        self.fail('Active orphan child lost its lease')
            finally:
                child.communicate(b'', timeout=5)
            with module.admission(directory, family='marketing', intent=intent(2), timeout=0):
                pass

    def test_refuses_unbound_or_invalid_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in [{}, {**intent(), 'outputSha256': 'not exact'}, {**intent(), 'aspect': 'both'}]:
                with self.assertRaises(ValueError):
                    with module.admission(directory, family='marketing', intent=value, timeout=0):
                        pass


if __name__ == '__main__':
    unittest.main()
