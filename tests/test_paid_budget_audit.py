from contextlib import closing
import fcntl
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import PaidCache, atomic_json, file_hash, request_key
from paid_budget_audit import audit


class PaidBudgetAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = Path(self.tmp.name)
        self.first = self.owner('a', 2000, 228)
        self.latest = self.owner('b', 9371, 629)
        self.payload = {'keyword': 'maths tutor Amersham', 'depth': 100}
        self.envelope = self.response('task-a', .002)
        self.key = self.settle(self.first, self.payload, self.envelope)
        self.unknown = {'keyword': 'english tutor Amersham', 'depth': 100}
        self.hold(self.first, self.unknown)
        cache = PaidCache(self.latest, 9371, 629)
        cache.import_receipt('maps', self.payload, self.envelope)
        cache.import_unsettled('maps', self.unknown, 'Inherited unknown request')
        cache.db.close()
        self.settle(self.latest, {'keyword': '11 plus tutor Amersham', 'depth': 100}, self.response('task-b', .002))

    def owner(self, suffix, budget, prior):
        root = self.history / ('tutor-lead-pathways-' + suffix)
        cache = PaidCache(root, budget, prior)
        cache.db.close()
        (root / 'run.lock').touch()
        atomic_json(root / 'runtime.json', {'phase': 'complete'})
        atomic_json(root / 'manifest.json', {'sharedCapCents': 10000,
            'priorExposureCents': prior, 'reservedDecisionsLifetimeCents': 300,
            'launchdLabels': {'run': 'com.jud.' + root.name}})
        return root

    @staticmethod
    def response(task, cost):
        return {'status_code': 20000, 'cost': cost,
                'tasks': [{'id': task, 'status_code': 20000, 'cost': cost, 'result': []}]}

    def settle(self, root, payload, response):
        with closing(sqlite3.connect(root / 'paid.sqlite')) as db:
            budget, prior = db.execute('SELECT budget,prior FROM settings').fetchone()
        cache = PaidCache(root, budget, prior)
        key = request_key('maps', payload)
        cache.reserve(key, 'maps', payload, 100)
        cache.settle(key, response)
        cache.db.close()
        return key

    def hold(self, root, payload):
        with closing(sqlite3.connect(root / 'paid.sqlite')) as db:
            budget, prior = db.execute('SELECT budget,prior FROM settings').fetchone()
        cache = PaidCache(root, budget, prior)
        key = request_key('maps', payload)
        cache.reserve(key, 'maps', payload, 100)
        cache.mark_uncertain(key, 'Unknown outcome')
        cache.db.close()

    def run_audit(self, probe=lambda _: 'stopped'):
        return audit(self.history, self.latest, self.first, owner_probe=probe)

    def rewrite_receipt(self, change):
        path = self.latest / 'receipts' / (self.key + '.json')
        value = json.loads(path.read_text())
        change(value)
        atomic_json(path, value)
        with closing(sqlite3.connect(self.latest / 'paid.sqlite')) as db, db:
            db.execute('UPDATE requests SET receipt_hash=? WHERE key=?', (file_hash(path), self.key))

    def test_provider_receipts_are_counted_once_and_reservations_are_retained(self):
        before = {str(p): file_hash(p) for p in self.history.glob('*/paid.sqlite')}
        with patch('socket.socket.connect', side_effect=AssertionError('Offline audit')):
            result = self.run_audit()
        self.assertEqual(result['verifiedSettledProviderCostNano'], 4_000_000)
        self.assertEqual(result['verifiedSettledRequests'], 2)
        self.assertEqual(result['uncertainReservationsCents'], 100)
        self.assertEqual(result['initialBaselineBufferCents'], 228)
        self.assertEqual(result['originalClassificationReserveCents'], 300)
        self.assertEqual(result['proposedPriorExposureCents'], 629)
        self.assertEqual(result['potentialHeadroomWholeCents'], 9371)
        self.assertEqual(result['proposedPriorExposureCents'] + result['potentialHeadroomWholeCents'], 10000)
        self.assertFalse(result['dispatchEnabled'])
        self.assertEqual(before, {str(p): file_hash(p) for p in self.history.glob('*/paid.sqlite')})

    def test_duplicate_unsettled_submissions_keep_both_reservations(self):
        self.hold(self.latest, self.unknown)
        # Replace the imported placeholder with an originating unknown attempt.
        with closing(sqlite3.connect(self.latest / 'paid.sqlite')) as db, db:
            db.execute("UPDATE requests SET imported=0,reservation=100 WHERE key=?",
                       (request_key('maps', self.unknown),))
        result = self.run_audit()
        self.assertEqual(result['uncertainRequests'], 1)
        self.assertEqual(result['uncertainOriginAttempts'], 2)
        self.assertEqual(result['uncertainReservationsCents'], 200)

    def test_active_locked_or_nonterminal_owners_cannot_be_audited(self):
        with self.assertRaisesRegex(ValueError, 'running'):
            self.run_audit(lambda _: 'running')
        with (self.first / 'run.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.run_audit()
        atomic_json(self.first / 'runtime.json', {'phase': 'running'})
        with self.assertRaisesRegex(ValueError, 'terminal'):
            self.run_audit()

    def test_modified_receipt_or_rehashed_forged_payload_cannot_free_budget(self):
        path = self.latest / 'receipts' / (self.key + '.json')
        original = path.read_bytes()
        path.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.run_audit()
        path.write_bytes(original)
        self.rewrite_receipt(lambda value: value['payload'].update(keyword='forged'))
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.run_audit()

    def test_missing_historical_request_or_originating_hold_is_rejected(self):
        with closing(sqlite3.connect(self.latest / 'paid.sqlite')) as db, db:
            db.execute('DELETE FROM requests WHERE key=?', (self.key,))
        with self.assertRaisesRegex(ValueError, 'omits historical'):
            self.run_audit()

    def test_imported_unknown_without_an_originating_reservation_is_rejected(self):
        with closing(sqlite3.connect(self.first / 'paid.sqlite')) as db, db:
            db.execute('DELETE FROM requests WHERE key=?', (request_key('maps', self.unknown),))
        with self.assertRaisesRegex(ValueError, 'originating reservation'):
            self.run_audit()

    def test_inconsistent_or_nonfinite_provider_cost_is_rejected(self):
        self.rewrite_receipt(lambda value: value['response']['tasks'][0].update(cost=.003))
        with self.assertRaisesRegex(ValueError, 'cost'):
            self.run_audit()
        for amount in ('NaN', 'Infinity', '-0.01'):
            with self.subTest(amount=amount):
                def change(value):
                    value['response']['cost'] = amount
                    value['response']['tasks'][0]['cost'] = amount
                self.rewrite_receipt(change)
                with self.assertRaisesRegex(ValueError, 'cost'):
                    self.run_audit()

    def test_subnano_charges_round_up_before_the_headroom_calculation(self):
        self.settle(self.latest, {'keyword': 'primary tutor Amersham'}, self.response('task-nano', 1e-10))
        self.assertEqual(self.run_audit()['verifiedSettledProviderCostNano'], 4_000_001)

    def test_original_classification_reserve_cannot_be_released(self):
        path = self.latest / 'manifest.json'
        value = json.loads(path.read_text())
        value['reservedDecisionsLifetimeCents'] = 0
        atomic_json(path, value)
        with self.assertRaisesRegex(ValueError, 'classification reserve'):
            self.run_audit()

    def test_distinct_paid_submissions_for_one_key_cannot_be_deduplicated(self):
        self.rewrite_receipt(lambda value: value['response']['tasks'][0].update(id='another-task'))
        with closing(sqlite3.connect(self.latest / 'paid.sqlite')) as db, db:
            db.execute('UPDATE requests SET task_id=? WHERE key=?', ('another-task', self.key))
        with self.assertRaisesRegex(ValueError, 'conflicting paid'):
            self.run_audit()

    def test_symlinked_receipt_is_rejected(self):
        path = self.latest / 'receipts' / (self.key + '.json')
        saved = self.history / 'saved.json'
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.run_audit()


if __name__ == '__main__':
    unittest.main()
