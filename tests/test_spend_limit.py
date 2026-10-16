import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from decisions_classifier import DecisionsCache, BudgetExhausted
from discovery import PaidCache, request_key
import spend_limit


class SpendLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paid = PaidCache(self.root / 'paid', 5000, 0); self.addCleanup(self.paid.db.close)
        self.decisions = DecisionsCache(self.root / 'decisions.sqlite', ['Amersham'])
        self.addCleanup(self.decisions.close)
        self.auth = self.root / 'authorization.json'
        self.value = {'instruction': spend_limit.INSTRUCTION,
            'requestedAt': '2026-10-16T11:16:08+00:00', 'maximumFurtherSpendCents': 1500,
            'newMoneyAuthorizedCents': 0, 'includes': ['DataForSEO', 'Decisions classification'],
            'baseline': {'paidLedger': str(self.root / 'paid/paid.sqlite'),
                         'decisionsLedger': str(self.decisions.path), 'paidSpentCents': 0,
                         'paidHeldCents': 0, 'decisionsSpentNano': 0, 'decisionsHeldNano': 0}}
        self.auth.write_text(json.dumps(self.value)); spend_limit.install(self.auth)

    def paid_reserve(self, number, cents=100):
        payload = {'keyword': 'tutor ' + str(number)}
        return self.paid.reserve(request_key('organic', payload), 'organic', payload, cents)

    def fill_paid(self, cents):
        self.paid.db.execute("INSERT INTO requests(key,status,cost_cents,cost_usd,imported) VALUES('prior','succeeded',?,?,0)",
                             (cents, str(cents / 100)))
        self.paid.db.commit()

    def test_shared_ceiling_counts_both_providers_and_existing_reservations(self):
        for i in range(14): self.assertTrue(self.paid_reserve(i))
        self.assertIsNone(self.decisions.reserve('one', {}, 1_000_000_000))
        self.assertFalse(self.paid_reserve(15, 1))
        with self.assertRaises(BudgetExhausted): self.decisions.reserve('two', {}, 1)
        self.assertEqual(self.paid.usage()['remainingCents'], 0)
        self.assertEqual(self.decisions.summary()['remainingUsd'], 0)

    def test_uncertain_holds_survive_reopening_without_a_fresh_allowance(self):
        self.fill_paid(1400)
        self.assertTrue(self.paid_reserve(1))
        self.paid.mark_uncertain(request_key('organic', {'keyword': 'tutor 1'}), 'Unknown response')
        reopened = PaidCache(self.root / 'paid', 5000, 0); self.addCleanup(reopened.db.close)
        self.assertEqual(reopened.usage()['remainingCents'], 0)
        self.assertFalse(reopened.reserve('new', 'organic', {}, 1))
        spend_limit.install(self.auth)
        self.assertEqual(reopened.usage()['remainingCents'], 0)

    def test_existing_decision_identity_can_still_be_read_when_spending_is_exhausted(self):
        self.fill_paid(1400)
        self.decisions.reserve('one', {}, 1_000_000_000)
        self.assertEqual(self.decisions.reserve('one', {}, 1_000_000_000)['key'], 'one')
        self.assertEqual(self.decisions.db.execute('SELECT count(*) FROM decision_requests').fetchone()[0], 1)

    def test_missing_or_modified_authorization_fails_closed(self):
        self.value['maximumFurtherSpendCents'] = 1600
        self.auth.write_text(json.dumps(self.value))
        with self.assertRaises(ValueError): self.paid_reserve(1)
        with self.assertRaises(ValueError): self.decisions.reserve('one', {}, 1)
        self.auth.unlink()
        with self.assertRaises(FileNotFoundError): self.paid_reserve(2)

    def test_a_second_instruction_cannot_reset_the_installed_baseline(self):
        other = self.root / 'new-authorization.json'
        other.write_text(json.dumps(dict(self.value, requestedAt='2026-10-16T12:00:00+00:00')))
        with self.assertRaisesRegex(ValueError, 'cannot be reset'): spend_limit.install(other)

    def test_partial_or_disagreeing_installation_fails_closed(self):
        self.decisions.db.execute('DELETE FROM spend_limit'); self.decisions.db.commit()
        with self.assertRaises(ValueError): self.paid_reserve(1)

    def test_original_caps_and_configurations_are_unchanged(self):
        self.assertEqual(tuple(self.paid.db.execute('SELECT budget,prior FROM settings').fetchone()), (5000, 0))
        self.assertEqual(json.loads(self.decisions.db.execute('SELECT value FROM decision_config').fetchone()[0])['capNano'], 3_000_000_000)
        with self.assertRaises(BudgetExhausted): self.decisions.reserve('too-large', {}, 3_000_000_001)

    def test_cross_provider_concurrent_reservations_cannot_overspend(self):
        self.fill_paid(1400)
        def paid():
            cache = PaidCache(self.root / 'paid', 5000, 0)
            try: return cache.reserve('concurrent', 'organic', {}, 100)
            finally: cache.db.close()
        def decision():
            cache = DecisionsCache(self.decisions.path, ['Amersham'])
            try:
                cache.reserve('concurrent', {}, 1_000_000_000)
                return True
            except BudgetExhausted: return False
            finally: cache.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(paid), pool.submit(decision)]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.paid.usage()['remainingCents'], 0)


if __name__ == '__main__': unittest.main()
