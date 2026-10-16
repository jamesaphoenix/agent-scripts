import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
import launch
import qualify_inventory
from discovery import atomic_json


class CredentialCooldownTests(unittest.TestCase):
    def test_rate_limit_records_wait_and_next_attempt_does_not_read_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            reply=subprocess.CompletedProcess([],1,'sensitive-output','Too many requests. Client rate-limited.')
            with patch.object(launch.subprocess,'run',return_value=reply) as read:
                with self.assertRaises(launch.CredentialRateLimited):
                    launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN':'test'},root)
                with self.assertRaises(launch.CredentialRateLimited):
                    launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN':'test'},root)
            self.assertEqual(read.call_count,1)
            saved=(root/'credential-wait.json').read_text()
            self.assertNotIn('sensitive-output',saved)
            self.assertFalse(json.loads(saved)['paidRequestSubmitted'])

    def test_classification_resolves_only_one_key_and_drops_the_service_token(self):
        with patch.object(launch.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'test-key\n','')) as read:
            env=launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN':'test','ANTHROPIC_API_KEY':'unrelated'},classification=True)
        self.assertEqual(env,{'OPENAI_API_KEY':'test-key'})
        self.assertEqual(read.call_count,1)
        self.assertEqual(read.call_args.args[0][-1],'op://api-keys/OpenAI/api_key')

    def test_resident_keeps_polling_without_restarting_credentials_or_extending_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);atomic_json(root/'job.json',{'deadlineUnix':12});clock=[10]
            def run(_):atomic_json(root/'status.json',{'phase':'complete'})
            def sleep(seconds):clock[0]+=seconds
            with patch.object(qualify_inventory,'run',side_effect=run) as batches, \
                    patch.object(qualify_inventory,'STOP',False), \
                    patch.object(qualify_inventory.time,'time',side_effect=lambda:clock[0]), \
                    patch.object(qualify_inventory.time,'sleep',side_effect=sleep):
                qualify_inventory.resident(root,poll_seconds=120)
            self.assertEqual(batches.call_count,1)
            self.assertEqual(clock[0],12)
            self.assertEqual(json.loads((root/'job.json').read_text())['deadlineUnix'],12)

    def test_resident_honours_provider_and_budget_terminal_stops(self):
        for phase in ['api_unavailable','budget_reached']:
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);atomic_json(root/'job.json',{'deadlineUnix':9999999999})
                def run(_):atomic_json(root/'status.json',{'phase':phase})
                with patch.object(qualify_inventory,'run',side_effect=run) as batches, \
                        patch.object(qualify_inventory,'STOP',False),patch.object(qualify_inventory.time,'sleep') as sleep:
                    qualify_inventory.resident(root)
                self.assertEqual(batches.call_count,1);sleep.assert_not_called()


if __name__=='__main__':unittest.main()
