import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
import launch
import qualify_inventory
from incremental_discovery import endpoint_probe_pending
from discovery import atomic_json


class CredentialCooldownTests(unittest.TestCase):
    def test_timeout_exit_does_not_trigger_a_second_cli_read_inside_the_shell_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / launch.CURRENT_CLASSIFICATION_STAGE
            root.mkdir()
            atomic_json(root / 'job.json', {'deadlineUnix': 1792361214})
            atomic_json(root / 'credential-quota-check.json', {
                'observedAt': '2026-10-18T17:41:17Z', 'status': 'available', 'accountReadWriteRemaining': 962})
            bin_dir = base / 'bin'
            bin_dir.mkdir()
            counter = base / 'attempts'
            fake_op = bin_dir / 'op'
            fake_op.write_text('#!/bin/sh\nprintf "%s\\n" attempt >> ' + shlex.quote(str(counter)) + '\nexit 142\n')
            fake_op.chmod(0o700)
            environment = {'OP_SERVICE_ACCOUNT_TOKEN': 'test', 'TMPDIR': tmp,
                           'PATH': str(bin_dir) + os.pathsep + os.defpath}
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    launch.runner_environment(environment, root, classification=True)
            self.assertEqual(counter.read_text().splitlines(), ['attempt'])

    def test_current_task_does_not_repeat_a_failed_non_rate_limited_secret_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / launch.CURRENT_CLASSIFICATION_STAGE
            root.mkdir()
            atomic_json(root / 'job.json', {'deadlineUnix': 1792361214})
            atomic_json(root / 'credential-quota-check.json', {
                'observedAt': '2026-10-18T17:41:17Z', 'status': 'available', 'accountReadWriteRemaining': 962})
            reply = subprocess.CompletedProcess([], 1, '', 'Authentication failed')
            with patch.object(launch.subprocess, 'run', return_value=reply) as read:
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN': 'test'}, root, classification=True)
            self.assertEqual(read.call_count, 1)
            self.assertFalse((root / 'credential-scratch/openai-api-key').exists())

    def test_new_window_requires_post_reset_quota_check_then_caches_one_secret_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/launch.CURRENT_CLASSIFICATION_STAGE
            root.mkdir()
            atomic_json(root/'job.json', {'deadlineUnix': 1792361214})
            reply=subprocess.CompletedProcess([],0,'test-key\n','')
            with patch.object(launch.subprocess,'run',return_value=reply) as read:
                with self.assertRaises(launch.CredentialRateLimited):
                    launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN':'test'},root,classification=True)
                read.assert_not_called()
                atomic_json(root/'credential-quota-check.json', {
                    'observedAt':'2026-10-18T15:22:20Z','status':'available','accountReadWriteRemaining':1})
                first=launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN':'test'},root,classification=True)
                second=launch.runner_environment({},root,classification=True)
            self.assertEqual(first['OPENAI_API_KEY'],'test-key')
            self.assertEqual(second['OPENAI_API_KEY'],'test-key')
            self.assertEqual(read.call_count,1)
            self.assertEqual(os.stat(root/'credential-scratch').st_mode & 0o777,0o700)
            self.assertEqual(os.stat(root/'credential-scratch/openai-api-key').st_mode & 0o777,0o600)

    def test_new_window_rate_limit_stops_secret_retries_until_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/launch.CURRENT_CLASSIFICATION_STAGE
            root.mkdir()
            atomic_json(root/'job.json', {'deadlineUnix': 1792361214})
            atomic_json(root/'credential-quota-check.json', {
                'observedAt':'2026-10-18T15:22:20Z','status':'available','accountReadWriteRemaining':1})
            reply=subprocess.CompletedProcess([],1,'','Too many requests. Client rate-limited.')
            with patch.object(launch.subprocess,'run',return_value=reply) as read:
                with self.assertRaises(launch.CredentialRateLimited):
                    launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN':'test'},root,classification=True)
                with self.assertRaises(launch.CredentialRateLimited):
                    launch.runner_environment({'OP_SERVICE_ACCOUNT_TOKEN':'test'},root,classification=True)
            self.assertEqual(read.call_count,1)
            self.assertEqual(json.loads((root/'credential-wait.json').read_text())['nextAttemptUnix'],1792361214)

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

    def test_regular_discovery_waits_for_the_current_probe_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);probe=root/'probe';probe.mkdir()
            atomic_json(root/'endpoint-probe-priority.json',{'probeRoot':str(probe),'sourceSha':'b'*40})
            atomic_json(probe/'status.json',{'loadedSourceSha':'a'*40,'phase':'complete'})
            self.assertTrue(endpoint_probe_pending(root))
            atomic_json(probe/'status.json',{'loadedSourceSha':'b'*40,'phase':'running'})
            self.assertTrue(endpoint_probe_pending(root))
            atomic_json(probe/'status.json',{'loadedSourceSha':'b'*40,'phase':'complete'})
            self.assertFalse(endpoint_probe_pending(root))


if __name__=='__main__':unittest.main()
