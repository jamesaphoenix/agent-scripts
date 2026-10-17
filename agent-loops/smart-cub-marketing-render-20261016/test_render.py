import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('render_queue', Path(__file__).with_name('render.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PhaseBoundaryTests(unittest.TestCase):
    def test_hold_keeps_completed_ctas_and_never_owns_a_render_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hold = root / 'workflow/marketing-revision-render.HOLD'
            hold.parent.mkdir()
            hold.touch()
            state = {'completedSelections': ['ctas'], 'renderControllerPid': 123, 'activeSelection': 'ctas'}
            checkpoints = []
            with mock.patch.object(module, 'RUN', root), \
                 mock.patch.object(module, 'save', side_effect=lambda value: checkpoints.append(copy.deepcopy(value))), \
                 mock.patch.object(module.time, 'sleep', side_effect=lambda _: hold.unlink()):
                module.wait_for_marketing_phase(state)
            self.assertEqual(checkpoints[0]['phase'], 'waiting-for-marketing-phase-release')
            self.assertNotIn('renderControllerPid', checkpoints[0])
            self.assertNotIn('activeSelection', checkpoints[0])
            self.assertEqual(state['completedSelections'], ['ctas'])
            self.assertEqual(state['phase'], 'rendering')
            self.assertNotIn('holdFile', state)

    def test_unreleased_hold_times_out_without_launching_any_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hold = root / 'workflow/marketing-revision-render.HOLD'
            hold.parent.mkdir()
            hold.touch()
            with mock.patch.object(module, 'RUN', root), mock.patch.object(module, 'save'), \
                 mock.patch.object(module.time, 'monotonic', side_effect=[0, 7201]), \
                 mock.patch.object(module.subprocess, 'Popen') as launch:
                with self.assertRaises(TimeoutError):
                    module.wait_for_marketing_phase({'completedSelections': ['ctas']})
                launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
