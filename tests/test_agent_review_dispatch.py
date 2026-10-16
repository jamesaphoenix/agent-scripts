import datetime
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import agent_review_dispatch as dispatch
from discovery import atomic_json, file_hash


class AgentReviewTests(unittest.TestCase):
    def setup_job(self, directory):
        root = Path(directory) / 'campaign/agent-reviews';root.mkdir(parents=True)
        deadline = datetime.datetime(2026, 10, 16, 18, 50, 2, tzinfo=datetime.timezone.utc)
        atomic_json(root.parent / 'authorization.json', {'deadline': deadline.isoformat()})
        atomic_json(root / 'job.json', {'threadId': dispatch.THREAD, 'intervalSeconds': 1800,
            'authorizationSha256': file_hash(root.parent / 'authorization.json'),
            'deadlineUnix': deadline.timestamp(), 'instruction': 'No, you should check it every 30 mins',
            'launchdLabel': 'com.jud.tutor-agent-reviews-20261016'})
        now = datetime.datetime(2026, 10, 16, 10, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
        return root, now, deadline.timestamp()

    def test_slots_align_to_half_hours_and_final_deadline(self):
        self.assertEqual(dispatch.review_slot(3611, 10000), 3600)
        self.assertEqual(dispatch.review_slot(5410, 10000), 5400)
        self.assertEqual(dispatch.review_slot(10001, 10000), 10000)

    def test_remote_queue_is_pinned_to_same_thread_and_shell_quotes_prompt(self):
        prompt = 'Inspect $(touch /tmp/unwanted) `secrets` "safely"'
        command = dispatch.queue_command(Path('/runtime/reviews'), prompt)
        self.assertIn('StrictHostKeyChecking=yes', command)
        remote = shlex.split(command[-1])
        self.assertEqual(remote[-1], prompt)
        self.assertEqual(remote[remote.index('--thread') + 1], dispatch.THREAD)
        self.assertIn('-u', remote)
        self.assertNotIn('--model', remote)

    def test_queue_receipt_is_not_agent_completion_and_is_not_submitted_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root, now, _ = self.setup_job(directory)
            accepted = subprocess.CompletedProcess([], 0,
                'Queued message 01a14412-30fc-79e3-8acb-4b09f37d9736 for thread ' + dispatch.THREAD + '.\n', '')
            with patch.object(dispatch, 'snapshot', return_value={'phase': 'running'}), \
                    patch.object(dispatch.subprocess, 'run', return_value=accepted) as queue:
                dispatch.check(root, now)
                dispatch.check(root, now + 60)
                self.assertEqual(queue.call_count, 1)
            receipt = json.loads(next((root / 'deliveries').glob('*.json')).read_text())
            self.assertEqual(receipt['status'], 'queued')
            self.assertFalse(receipt['inspectionComplete'])
            self.assertFalse((root / 'reviews').exists())

    def test_uncertain_queue_delivery_is_held_without_repeating_model_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            root, now, _ = self.setup_job(directory)
            with patch.object(dispatch, 'snapshot', return_value={}), \
                    patch.object(dispatch.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 30)) as queue:
                dispatch.check(root, now)
                dispatch.check(root, now + 60)
                self.assertEqual(queue.call_count, 1)
            receipt = json.loads(next((root / 'deliveries').glob('*.json')).read_text())
            self.assertEqual(receipt['status'], 'delivery_unconfirmed')
            self.assertFalse(receipt['inspectionComplete'])

    def test_actual_agent_acknowledgement_prevents_duplicate_review_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            root, now, _ = self.setup_job(directory)
            atomic_json(root / 'reviews' / (dispatch.slot_id(now) + '.json'),
                {'threadId': dispatch.THREAD, 'reviewedAt': '2026-10-16T10:01:00Z',
                 'findings': 'Reviewed source yield, budget and queues; healthy.'})
            with patch.object(dispatch.subprocess, 'run') as queue:
                dispatch.check(root, now)
                queue.assert_not_called()

    def test_schedule_does_not_extend_discovery_or_create_new_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            root, now, deadline = self.setup_job(directory)
            prompt = dispatch.review_prompt(root, now, deadline)
            self.assertIn('same conversation', prompt)
            self.assertIn('Do not spawn another agent', prompt)
            self.assertIn('No new paid allowance', prompt)
            with patch.object(dispatch, 'retire') as retire, patch.object(dispatch.subprocess, 'run') as queue:
                dispatch.check(root, deadline + 1801)
                retire.assert_called_once()
                queue.assert_not_called()
            self.assertTrue((root / 'finished.json').exists())


if __name__ == '__main__':
    unittest.main()
