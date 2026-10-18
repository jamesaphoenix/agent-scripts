import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent-loops/tutor-lead-pathways'))
import tutor_web_review as review


class TutorWebReviewTests(unittest.TestCase):
    def setUp(self):
        self.auth = json.loads((ROOT / 'tests/fixtures/tutor_web_research_15h_authorization.json').read_text())
        self.now = datetime.datetime.fromisoformat('2026-10-18T19:00:01+00:00')
        self.agent = {'result': {'agent': {'pane_id': 'wA:p1', 'cwd': review.EXPECTED_AGENT_CWD,
                                         'agent_status': 'working'}}}
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.control = Path(self.temporary.name)

    def test_authority_does_not_allow_extra_time_money_or_old_owner_extension(self):
        review.validate(self.auth)
        for key, value in [('deadline', '2026-10-19T10:44:34+00:00'),
                           ('newMoneyAuthorizedCents', 1), ('maximumSdkTurns', True),
                           ('existingWorkerDeadline', self.auth['deadline']),
                           ('paidDataForSeoSearchesAllowed', True)]:
            changed = dict(self.auth, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                review.validate(changed)

    def test_busy_agent_gets_one_review_without_script_writing_an_actual_ack(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(self.agent), '')

        with patch.object(review, 'CONTROL', self.control), \
                patch.object(review, 'load_authorization', return_value=self.auth), \
                patch.object(review.subprocess, 'run', side_effect=run):
            review.check(self.now)
            review.check(self.now + datetime.timedelta(seconds=60))
        self.assertEqual(len(calls), 2)
        self.assertIn('prompt', calls[1])
        self.assertIn('1000 additional', calls[1][-1])
        receipt = json.loads(next((self.control / 'deliveries').glob('*.json')).read_text())
        self.assertEqual(receipt['state'], 'delivered')
        self.assertFalse(receipt['actualAgentInspection'])
        self.assertFalse((self.control / 'reviews').exists())

    def test_uncertain_prompt_is_not_replayed(self):
        responses = [subprocess.CompletedProcess([], 0, json.dumps(self.agent), ''),
                     subprocess.TimeoutExpired(['herdr'], 20)]
        with patch.object(review, 'CONTROL', self.control), \
                patch.object(review, 'load_authorization', return_value=self.auth), \
                patch.object(review.subprocess, 'run', side_effect=responses) as run:
            review.check(self.now)
            review.check(self.now)
            self.assertEqual(run.call_count, 2)
        receipt = json.loads(next((self.control / 'deliveries').glob('*.json')).read_text())
        self.assertEqual(receipt['state'], 'uncertain')

    def test_changed_agent_target_is_never_prompted(self):
        self.agent['result']['agent']['pane_id'] = 'w9:p1'
        response = subprocess.CompletedProcess([], 0, json.dumps(self.agent), '')
        with patch.object(review, 'CONTROL', self.control), \
                patch.object(review, 'load_authorization', return_value=self.auth), \
                patch.object(review.subprocess, 'run', return_value=response) as run:
            with self.assertRaises(ValueError):
                review.check(self.now)
            self.assertEqual(run.call_count, 1)

    def test_deadline_prompt_stops_search_and_requests_cleanup(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(self.agent), '')

        with patch.object(review, 'CONTROL', self.control), \
                patch.object(review, 'load_authorization', return_value=self.auth), \
                patch.object(review.subprocess, 'run', side_effect=run):
            review.check(datetime.datetime.fromisoformat(self.auth['deadline']))
        self.assertIn('STOP new searches', calls[-1][-1])
        receipt = json.loads(next((self.control / 'deliveries').glob('*.json')).read_text())
        self.assertTrue(receipt['finalReview'])

    def test_after_final_grace_only_dispatcher_is_stopped(self):
        after = datetime.datetime.fromisoformat(self.auth['deadline']) + datetime.timedelta(minutes=31)
        with patch.object(review, 'CONTROL', self.control), \
                patch.object(review, 'load_authorization', return_value=self.auth), \
                patch.object(review.subprocess, 'run') as run:
            review.check(after)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['/bin/launchctl', 'bootout'])
        self.assertTrue(command[-1].endswith(review.LABEL))
        receipt = json.loads((self.control / 'review-dispatch-final.json').read_text())
        self.assertFalse(receipt['actualAgentInspection'])
        self.assertFalse(receipt['cleanupReportPresent'])


if __name__ == '__main__':
    unittest.main()
