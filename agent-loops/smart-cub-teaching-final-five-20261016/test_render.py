import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('final_five_render', Path(__file__).with_name('render.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PredecessorTests(unittest.TestCase):
    def setup_run(self, directory):
        root = Path(directory)
        (root / 'workflow').mkdir()
        (root / 'darwin').mkdir()
        previous = {'state': 'complete_rendered_needs_review', 'exitCode': 0, 'pid': 11,
                    'renderControllerPid': 22,
                    'manifestSha256': '660f7f438ce6f0222f996c95a4f9a324c953d0aeb0cf76dd6fbc9722f2ac32bc'}
        path = root / 'workflow/teaching-follow-up-dispatch-status.json'
        path.write_text(json.dumps(previous))
        return root, path, previous

    def test_waits_for_predecessor_before_receipt_checks_and_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root, path, complete = self.setup_run(directory)
            path.write_text(json.dumps({'state': 'rendering'}))
            checkpoints = []
            with mock.patch.object(module, 'RUN', root), \
                 mock.patch.object(module, 'process_alive', return_value=False), \
                 mock.patch.object(module, 'predecessor_receipts', return_value=[{'id': 'four exact receipts'}]) as receipts, \
                 mock.patch.object(module.subprocess, 'check_output', return_value=str(root / 'darwin')), \
                 mock.patch.object(module, 'save', side_effect=lambda state: checkpoints.append(copy.deepcopy(state))), \
                 mock.patch.object(module.time, 'sleep', side_effect=lambda _: path.write_text(json.dumps(complete))), \
                 mock.patch.object(module.subprocess, 'Popen') as launch:
                proof = module.wait_for_predecessor({})
                self.assertEqual(checkpoints[0]['state'], 'waiting-for-follow-up')
                self.assertTrue(Path(proof['predecessorProof']).is_file())
                receipts.assert_called_once()
                # Explicit recovery from a later preflight failure revalidates the
                # same receipts while preserving the original immutable proof.
                self.assertEqual(module.wait_for_predecessor({}), proof)
                launch.assert_not_called()

    def test_only_matching_dead_predecessor_lease_can_be_removed(self):
        for matching in [False, True]:
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as directory:
                root, _, _ = self.setup_run(directory)
                lease = root / 'darwin/smart-cub-founder-render.lock'
                lease.mkdir()
                owner = lease / 'owner.json'
                owner.write_text(json.dumps({'pid': 22 if matching else 99,
                    'host': 'studio', 'cwd': str(module.APP)}))
                with mock.patch.object(module, 'RUN', root), \
                     mock.patch.object(module, 'process_alive', return_value=False), \
                     mock.patch.object(module.socket, 'gethostname', return_value='studio'), \
                     mock.patch.object(module, 'predecessor_receipts', return_value=[]), \
                     mock.patch.object(module.subprocess, 'check_output', return_value=str(root / 'darwin')):
                    if matching:
                        proof = module.wait_for_predecessor({})
                        self.assertFalse(lease.exists())
                        self.assertTrue((root / 'workflow/teaching-final-five-predecessor-lease-owner.json').is_file())
                        self.assertTrue(Path(proof['predecessorProof']).is_file())
                    else:
                        with self.assertRaises(AssertionError):
                            module.wait_for_predecessor({})
                        self.assertTrue(owner.is_file())

    def test_failed_predecessor_never_starts_successor(self):
        with tempfile.TemporaryDirectory() as directory:
            root, path, _ = self.setup_run(directory)
            path.write_text(json.dumps({'state': 'halted'}))
            with mock.patch.object(module, 'RUN', root), mock.patch.object(module.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'explicit reconciliation'):
                    module.wait_for_predecessor({})
                launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
