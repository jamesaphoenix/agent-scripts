import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import studio_review_check as review


class StudioReviewCheckTests(unittest.TestCase):
    def test_existing_final_snapshot_still_unloads_after_grace(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            deadline = datetime.datetime.fromisoformat('2026-10-17T21:03:13+00:00')
            (control / 'authorization.json').write_text(json.dumps({
                'deadline': deadline.isoformat(), 'monitorIntervalSeconds': 1800}))
            final = control / 'inspections' / '20261017T210313Z.json'
            final.parent.mkdir()
            final.write_text('{}')
            now = deadline + datetime.timedelta(minutes=31)
            with patch.object(review, 'CONTROL', control), patch.object(review.subprocess, 'run') as run:
                review.check(now)
            run.assert_called_once_with(['launchctl', 'bootout', f'gui/{review.os.getuid()}/{review.LABEL}'],
                                        capture_output=True, timeout=10)
            self.assertEqual(final.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
