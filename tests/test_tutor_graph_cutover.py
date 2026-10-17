import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import prepare_qualification_followon as qualification
from prepare_qualification_followon import validate_tutor_graph
from qualify_inventory import create_database
import studio_graph_review_check as review


class TutorGraphCutoverTests(unittest.TestCase):
    def setUp(self):
        self.auth = {
            'instruction': 'Go go for another 10-20 hours.',
            'precedingInstruction': 'Could you continually improving the crawl and only adding extra tutor sites to our graph.',
            'requestedAt': '2026-10-17T17:46:36+00:00',
            'deadline': '2026-10-18T09:46:36+00:00',
            'durationHours': 16, 'newMoneyAuthorizedCents': 0,
            'maximumRemainingSpendNano': 3798775900, 'sharedCombinedCeilingUsd': 15,
            'paidDataForSeoSearchesAllowed': False, 'decisionsClassificationAllowed': True,
            'maximumSdkTurns': 0, 'anthropicApiKeyAllowed': False, 'outreachAllowed': False,
            'monitorIntervalSeconds': 1800,
            'scopeSha256': '626d1ea33c5d87366917a34f22f3fc8deed5fde3b6227503afa2ffc79d10f5c6',
            'previousQualificationRoot': '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/tutor-qualification-20261017-free-12h',
            'previousProfileRoot': '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/bucks-private-tutors-20261017-free-12h',
        }

    def test_exact_window_and_no_extra_allowance(self):
        start, deadline = validate_tutor_graph(self.auth)
        self.assertEqual(deadline - start, datetime.timedelta(hours=16))
        for key, changed in [('deadline', '2026-10-18T10:46:36+00:00'),
                             ('newMoneyAuthorizedCents', 1),
                             ('maximumRemainingSpendNano', 3798775901),
                             ('previousProfileRoot', '/tmp/other'),
                             ('paidDataForSeoSearchesAllowed', True)]:
            with self.subTest(key=key):
                item = {**self.auth, key: changed}
                with self.assertRaises(ValueError):
                    validate_tutor_graph(item)

    def test_existing_final_snapshot_unloads_after_grace(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            (control / 'authorization.json').write_text(json.dumps(self.auth))
            final = control / 'inspections' / '20261018T094636Z.json'
            final.parent.mkdir()
            final.write_text('{}')
            deadline = datetime.datetime.fromisoformat(self.auth['deadline'])
            with patch.object(review, 'CONTROL', control), patch.object(review.subprocess, 'run') as run:
                review.check(deadline + datetime.timedelta(minutes=31))
            run.assert_called_once_with(['launchctl', 'bootout', f'gui/{review.os.getuid()}/{review.LABEL}'],
                                        capture_output=True, timeout=10)
            self.assertEqual(final.read_text(), '{}')

    def test_real_sixteen_hour_prepare_preserves_negative_and_uncertain_states(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            parent = base / 'previous'; parent.mkdir()
            child = base / 'new'
            with sqlite3.connect(parent / 'decisions.sqlite') as db:
                db.execute('CREATE TABLE decision_config(id INTEGER PRIMARY KEY,value TEXT)')
                db.execute('INSERT INTO decision_config VALUES(1,?)', ('sealed',))
            scope = parent / 'discovery-scope.json'; scope.write_text('{}')
            inventory = base / 'inventory.json'; inventory.write_text('[{"status":"research"}]')
            (parent / 'job.json').write_text(json.dumps({'scope': str(scope), 'input': str(inventory)}))
            (parent / 'status.json').write_text(json.dumps({'phase': 'complete', 'decisions': {'remainingUsd': 3.7987759}}))
            db = create_database(parent / 'candidates.sqlite')
            with db:
                db.execute("INSERT INTO candidates(key,candidate,state,error) VALUES('held','{}','fetch_held','403')")
                db.execute("INSERT INTO candidates(key,candidate,state) VALUES('negative','{}','excluded_irrelevant')")
                db.execute("INSERT INTO candidates(key,candidate,state) VALUES('uncertain','{}','decision_held')")
            db.close()
            auth = {**self.auth, 'previousQualificationRoot': str(parent.resolve())}
            auth_path = base / 'authorization.json'; auth_path.write_text(json.dumps(auth))
            start = datetime.datetime.fromisoformat(self.auth['requestedAt'])
            deadline = datetime.datetime.fromisoformat(self.auth['deadline'])
            profile = Path('/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/bucks-private-tutors-20261017-tutor-graph-16h')
            with patch.object(qualification, 'validate_tutor_graph', return_value=(start, deadline)), \
                 patch.object(qualification, 'validate_studio_free', side_effect=AssertionError('old validator reached')):
                job = qualification.prepare(parent, child, inventory, auth_path, profile)
            self.assertEqual(job['authorizedDeadline'], deadline.isoformat())
            self.assertEqual(job['startedAt'], start.isoformat())
            self.assertEqual(job['sharedCache']['path'], str(parent.resolve() / 'decisions.sqlite'))
            with sqlite3.connect(child / 'candidates.sqlite') as copied:
                self.assertEqual(dict(copied.execute('SELECT key,state FROM candidates')),
                                 {'held': 'fetch_held', 'negative': 'excluded_irrelevant', 'uncertain': 'decision_held'})


if __name__ == '__main__':
    unittest.main()
