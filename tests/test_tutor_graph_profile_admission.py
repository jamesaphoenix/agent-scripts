from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import directory_profiles

ROOT = Path('/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state')
GRAPH = ROOT / 'bucks-private-tutors-20261017-tutor-graph-16h'
OLD = ROOT / 'bucks-private-tutors-20261017-free-12h'


@unittest.skipUnless((GRAPH / 'job.json').exists() and (OLD / 'job.json').exists(),
                     'Requires the prepared Studio cutover fixtures')
class TutorGraphProfileAdmissionTests(unittest.TestCase):
    def test_validated_graph_stage_accepts_sixteen_hours_without_running(self):
        with patch.object(directory_profiles, 'run') as run:
            directory_profiles.main(['--run-dir', str(GRAPH), '--hours', '16',
                                     '--pages', '29892', '--target', '5000'])
        run.assert_called_once_with(GRAPH, [], 16.0, 29892, 5000)

    def test_previous_twelve_hour_stage_rejects_sixteen_hours(self):
        with patch.object(directory_profiles, 'run') as run:
            with self.assertRaises(SystemExit):
                directory_profiles.main(['--run-dir', str(OLD), '--hours', '16',
                                         '--pages', '29892', '--target', '5000'])
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
