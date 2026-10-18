import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from op_quota import account_quota, repair_report


class OpQuotaTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'type': 'token', 'action': 'read', 'limit': 1000, 'used': 10, 'remaining': 990, 'reset': 2000},
            {'type': 'account', 'action': 'read_write', 'limit': 1000, 'used': 38, 'remaining': 962, 'reset': 78050},
        ]

    def test_cli_list_and_legacy_object_select_shared_account_limit(self):
        for payload in (self.rows, {'quotas': self.rows}):
            with self.subTest(payload_type=type(payload).__name__):
                result = account_quota(payload)
                self.assertEqual(result['status'], 'available')
                self.assertEqual(result['accountReadWriteRemaining'], 962)

    def test_unknown_missing_or_duplicate_account_is_not_reported_as_exhaustion(self):
        for payload in (None, {}, [], self.rows[:1], self.rows + self.rows[1:], ['invalid']):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                account_quota(payload)

    def test_actual_zero_is_exhausted_and_invalid_counters_fail_closed(self):
        account = self.rows[1]
        self.assertEqual(account_quota([{**account, 'used': 1000, 'remaining': 0}])['status'], 'exhausted')
        for values in ({'remaining': -1}, {'remaining': True}, {'remaining': '962'}, {'used': 39}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                account_quota([{**account, **values}])

    def test_repair_reuses_saved_response_preserves_original_and_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / 'response.json', root / 'report.json'
            source.write_text(json.dumps(self.rows))
            original = {'status': 'error', 'secretReadAttempted': False, 'deadline': '2026-10-18T22:06:54Z'}
            output.write_text(json.dumps(original))
            report = repair_report(source, output, '2026-10-18T17:41:17Z')
            self.assertEqual(report['accountReadWriteRemaining'], 962)
            self.assertEqual(json.loads(Path(report['previousReceiptPath']).read_text()), original)
            self.assertEqual(report['deadline'], original['deadline'])
            self.assertFalse(report['secretReadAttempted'])
            with self.assertRaises(ValueError):
                repair_report(source, output, '2026-10-18T17:41:18Z')
