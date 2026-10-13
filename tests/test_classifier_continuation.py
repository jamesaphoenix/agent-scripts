import fcntl
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
import campaign_watch
from discovery import atomic_json
from configuration import focused_roots


class ClassifierContinuationTests(unittest.TestCase):
    def test_profile_seed_bridge_includes_only_a_prepared_remainder_without_changing_manifest(self):
        from discovery import PaidCache
        from profile_seeds import write_seed_batch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent, current = root / 'parent', root / 'tutor-focused-20261013-12h'
            manifest = {'focusedRoots': [str(parent), str(current)], 'focusedParent': str(parent),
                'focusedContinuation': str(current)}
            atomic_json(root / 'manifest.json', manifest)
            self.assertEqual(focused_roots(manifest), [parent, current])
            remainder = current.with_name(current.name + '-remainder')
            atomic_json(remainder / 'job.json', {'continuedFrom': str(current)})
            url = 'https://www.tutorhunt.com/users/jane-smith/'
            atomic_json(remainder / 'profile-candidates-not-in-baseline.json', [{'url': url}])
            cache = PaidCache(root, 100, 0)
            try:
                result = write_seed_batch(root, root / 'profiles', cache)
                self.assertEqual(result['urls'], 1)
                self.assertEqual(result['receiptAttributions'][0]['focusedExport'],
                                 str(remainder / 'profile-candidates-not-in-baseline.json'))
                self.assertEqual(cache.usage()['spentCents'], 0)
                self.assertEqual(json.loads((root / 'manifest.json').read_text()), manifest)
            finally:
                cache.db.close()

    def fixture(self, root):
        parent, current = root / 'parent', root / 'tutor-focused-20261013-12h'
        job = {'focusedParent': str(parent), 'focusedContinuation': str(current),
            'authorizedDeadline': '2026-10-13T23:43:51+00:00', 'keywordIndexPath': str(root / 'index.sqlite'),
            'crawlerBinary': str(root / 'crawler'), 'focusedHistory': str(root / 'history'),
            'identityBaselineCsv': str(root / 'baseline.csv')}
        atomic_json(current / 'job.json', {'cache': str(root / 'shared-decisions.sqlite'),
            'policy': {'max_pages': 10000}})
        atomic_json(current / 'status.json', {'status': 'complete', 'attempts': 10000,
            'decisions': {'remainingUsd': 1.1}})
        atomic_json(root / 'classifier-installed.json', {'runDir': str(current)})
        return job, current

    def test_capped_terminal_parent_can_continue_with_same_cache_deadline_and_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, current = self.fixture(root)
            before = (current / 'job.json').read_bytes()
            def prepare(target, *args, **kwargs):
                self.assertEqual(args[1], root / 'shared-decisions.sqlite')
                self.assertEqual(args[5], 20000)
                self.assertEqual(kwargs['previous'], current)
                self.assertEqual(kwargs['directory_pages'], 4000)
                self.assertEqual(kwargs['authorized_deadline'], 1791935031)
                atomic_json(target / 'status.json', {'status': 'prepared', 'attempts': 10000})
                atomic_json(target / 'job.json', {'continuedFrom': str(current)})
            with patch('campaign_watch.time.time', return_value=1791907000), \
                 patch('campaign_watch.prepare_focused', side_effect=prepare) as copying, \
                 patch('campaign_watch.subprocess.run') as commands:
                commands.return_value.returncode = 0
                commands.return_value.stdout = 'source-sha\n'
                self.assertEqual(campaign_watch.continue_classifier(root, job), 'prepared')
                self.assertEqual(campaign_watch.continue_classifier(root, job), 'prepared')
            copying.assert_called_once()
            self.assertEqual((current / 'job.json').read_bytes(), before)
            self.assertTrue((root / 'classifier-remainder-installed.json').exists())

    def test_running_budget_failed_or_expired_stage_cannot_be_extended(self):
        for status in ['running', 'budget_exhausted', 'api_unavailable']:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job, current = self.fixture(root)
                value = json.loads((current / 'status.json').read_text())
                value['status'] = status
                atomic_json(current / 'status.json', value)
                with patch('campaign_watch.time.time', return_value=1791907000), \
                     patch('campaign_watch.prepare_focused') as copying:
                    self.assertEqual(campaign_watch.continue_classifier(root, job), status)
                    copying.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, _ = self.fixture(root)
            with patch('campaign_watch.time.time', return_value=1791935031), \
                 patch('campaign_watch.prepare_focused') as copying:
                self.assertEqual(campaign_watch.continue_classifier(root, job), 'deadline_reached')
                copying.assert_not_called()

    def test_parent_finalization_lock_prevents_copying_and_counter_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, current = self.fixture(root)
            with (current / 'run.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch('campaign_watch.time.time', return_value=1791907000), \
                     patch('campaign_watch.prepare_focused') as copying, \
                     patch('campaign_watch.subprocess.run') as commands:
                    commands.return_value.stdout = 'source-sha\n'
                    self.assertEqual(campaign_watch.continue_classifier(root, job), 'previous_classifier_finalizing')
                    copying.assert_not_called()

    def test_no_page_ceiling_or_no_remaining_classification_budget_cannot_start_another_stage(self):
        for value in [{'status': 'complete', 'attempts': 9999, 'decisions': {'remainingUsd': 1}},
                      {'status': 'complete', 'attempts': 10000, 'decisions': {'remainingUsd': 0}}]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job, current = self.fixture(root)
                atomic_json(current / 'status.json', value)
                with patch('campaign_watch.time.time', return_value=1791907000), \
                     patch('campaign_watch.prepare_focused') as copying:
                    self.assertEqual(campaign_watch.continue_classifier(root, job), 'complete')
                    copying.assert_not_called()

    def test_terminal_cleanup_waits_for_the_remainder_and_retains_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, current = self.fixture(root)
            job['focusedRoots'] = [str(current)]
            profiles = root / 'profiles'
            atomic_json(root / 'runtime.json', {'phase': 'complete'})
            atomic_json(profiles / 'status.json', {'phase': 'stage_complete'})
            remainder = current.with_name(current.name + '-remainder')
            atomic_json(remainder / 'job.json', {'continuedFrom': str(current)})
            atomic_json(remainder / 'status.json', {'status': 'running'})
            scratch = root / 'sdk/ssh-known-hosts'
            scratch.parent.mkdir()
            scratch.write_text('fixture')
            self.assertFalse(campaign_watch.terminal_cleanup(root, profiles, job))
            self.assertTrue(scratch.exists())
            atomic_json(remainder / 'status.json', {'status': 'complete'})
            self.assertTrue(campaign_watch.terminal_cleanup(root, profiles, job))
            self.assertFalse(scratch.exists())
            self.assertIn(str(root / 'paid.sqlite'), json.loads((root / 'cleanup.json').read_text())['retained'])


if __name__ == '__main__':
    unittest.main()
