import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import PaidCache, atomic_json
import run as runner


class RunnerSnapshotTests(unittest.TestCase):
    def snapshot(self, root, cache, source='a' * 40):
        job = {'runId': 'test', 'startedAt': '2026-10-13T11:43:51Z',
               'deadline': '2026-10-13T23:43:51Z', 'sourceSha': 'b' * 40}
        state = {'phase': 'running', 'pid': 123, 'completedQueries': [], 'crawlNumber': 0,
                 'errors': [], 'pathFailures': {}, 'effectiveSourceSha': source}
        return runner.runner_snapshot(root, job, cache, state)

    def test_optional_deployment_report_cannot_stop_a_budget_preserving_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = PaidCache(root, 100, 0)
            report = root / 'deployment/latest.json'
            report.parent.mkdir()
            try:
                before = cache.usage()
                for contents in ['{}', '[]', 'invalid JSON']:
                    with self.subTest(contents=contents):
                        report.write_text(contents)
                        result = self.snapshot(root, cache)
                        self.assertEqual(result['effectiveSourceSha'], 'a' * 40)
                        self.assertEqual(result['budget'], before)
                        self.assertEqual(result['pid'], 123)
            finally:
                cache.db.close()

    def test_later_deployment_report_cannot_change_a_running_owners_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = PaidCache(root, 100, 0)
            try:
                atomic_json(root / 'deployment/latest.json', {'sourceSha': 'c' * 40})
                result = self.snapshot(root, cache)
                self.assertEqual(result['effectiveSourceSha'], 'a' * 40)
                self.assertEqual(result['preparedSourceSha'], 'b' * 40)
            finally:
                cache.db.close()

    def test_unknown_loaded_version_is_not_presented_as_the_prepared_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = PaidCache(root, 100, 0)
            try:
                result = self.snapshot(root, cache, None)
                self.assertIsNone(result['effectiveSourceSha'])
                self.assertEqual(result['preparedSourceSha'], 'b' * 40)
            finally:
                cache.db.close()

    def test_loaded_source_capture_is_bounded_and_nonfatal(self):
        with mock.patch.object(runner.subprocess, 'run') as git:
            git.return_value = subprocess.CompletedProcess([], 0, 'a' * 40 + '\n')
            self.assertEqual(runner.loaded_source_sha(), 'a' * 40)
            self.assertEqual(git.call_args.kwargs['timeout'], 10)
            git.return_value = subprocess.CompletedProcess([], 0, 'not a commit')
            self.assertIsNone(runner.loaded_source_sha())
            git.side_effect = subprocess.TimeoutExpired('git', 10)
            self.assertIsNone(runner.loaded_source_sha())


if __name__ == '__main__':
    unittest.main()
