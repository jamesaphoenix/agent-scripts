import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import incremental_discovery as discovery
from discovery import PaidCache, atomic_json, request_key, file_hash
from keyword_expansion import SearchIndex
from prepare_recovery import seal_drained_profile


class RecoveryTests(unittest.TestCase):
    def auth(self):
        start = datetime.datetime.now(datetime.timezone.utc)
        return {'instruction': discovery.RECOVERY_INSTRUCTION, 'requestedAt': start.isoformat(),
                'deadline': (start + datetime.timedelta(hours=10)).isoformat(), 'newBudgetCents': 0,
                'originalBudgetCapCents': 5000, 'monitorIntervalSeconds': 1800,
                'bankManifestSha256': 'a' * 64}

    def response(self, code):
        return {'status_code': 20000, 'cost': .002, 'tasks': [{'status_code': code, 'cost': .002, 'id': str(code)}]}

    def test_empty_results_do_not_trigger_an_outage(self):
        state = {}
        for _ in range(8):
            self.assertIsNone(discovery.record_health(state, {'tactic': 'operators'},
                {'status': 'failed'}, self.response(40102), now=100))
        self.assertEqual(state['tacticHealth']['operators']['failures'], 0)
        self.assertEqual(state['consecutiveUncertain'], 0)

    def test_five_engine_errors_pause_only_the_affected_tactic(self):
        state = {'completedQueries': 6, 'tactics': {}}
        for _ in range(5):
            self.assertIsNone(discovery.record_health(state, {'tactic': 'operators'},
                {'status': 'failed'}, self.response(40101), now=100))
        self.assertGreater(state['tacticHealth']['operators']['cooldownUntil'], 100)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);cache = PaidCache(root / 'bank', 5000, 0);index = SearchIndex(root / 'index.sqlite')
            try:
                plan = discovery.query_plan([{'name': 'Amersham', 'coordinate': '51.67,-0.61'}])
                operators = dict(plan[-1], tactic='operators')
                maps_query = next(q for q in plan if q['lane'] == 'maps')
                with patch.object(discovery.time, 'time', return_value=101):
                    self.assertEqual(discovery.choose_query([operators, maps_query], cache, index, state)['lane'], 'maps')
            finally:
                cache.db.close();index.close()

    def test_plain_maps_and_organic_cannot_be_starved_by_novelty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);cache = PaidCache(root / 'bank', 5000, 0);index = SearchIndex(root / 'index.sqlite')
            try:
                plan = discovery.query_plan([{'name': 'Amersham', 'coordinate': '51.67,-0.61'}])
                stats = {'local_directories': {'requests': 10, 'newRelevantOwners': 999999}}
                for number, expected in [(60, 'plain:maps'), (61, 'plain:organic')]:
                    picked = discovery.choose_query(plan, cache, index, {'completedQueries': number, 'tactics': stats})
                    self.assertEqual(picked['tactic'], expected)
            finally:
                cache.db.close();index.close()

    def test_account_errors_stop_immediately_and_uncertain_submissions_stay_bounded(self):
        for code in [40100, 40104, 40200, 40201, 40203, 40207, 40210]:
            self.assertEqual(discovery.record_health({}, {'tactic': 'maps'}, {'status': 'failed'},
                self.response(code)), 'account_blocked')
        self.assertEqual(discovery.result_kind({'status': 'uncertain', 'error': 'HTTPError: HTTP Error 401: Unauthorized'}, None), 'account_blocked')
        state = {}
        for i in range(5):
            result = discovery.record_health(state, {'tactic': 'maps'}, {'status': 'uncertain'}, None)
            self.assertEqual(result, 'api_unavailable' if i == 4 else None)

    def test_legacy_stop_recovery_requires_settled_receipts_and_never_releases_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = PaidCache(Path(directory), 5000, 0)
            try:
                for i, code in enumerate([40101, 40102, 40101, 40102, 40101]):
                    payload = {'keyword': 'tutor Amersham ' + str(i)};key = request_key('organic', payload)
                    cache.reserve(key, 'organic', payload, 5);cache.settle(key, self.response(code))
                before = cache.usage()
                self.assertTrue(discovery.recoverable_stop({'phase': 'api_unavailable'}, cache))
                self.assertEqual(before, cache.usage())
                payload = {'keyword': 'another tutor'};key = request_key('organic', payload)
                cache.reserve(key, 'organic', payload, 5);cache.mark_uncertain(key, 'Unverified outcome')
                before = cache.usage()
                self.assertFalse(discovery.recoverable_stop({'phase': 'api_unavailable'}, cache))
                self.assertEqual(cache.usage(), before)
                self.assertEqual(cache.usage()['uncertainCents'], 5)
            finally:
                cache.db.close()

    def test_ten_hour_window_does_not_authorize_more_money_or_different_monitoring(self):
        auth = self.auth();discovery.validate_recovery_authorization(auth)
        for key, value in [('newBudgetCents', 5000), ('monitorIntervalSeconds', 900),
                           ('deadline', auth['requestedAt']), ('instruction', 'keep going')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                discovery.validate_recovery_authorization(dict(auth, **{key: value}))

    def test_runtime_continues_past_engine_errors_and_empty_results_to_maps_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);plan = discovery.query_plan([{'name': 'Amersham', 'coordinate': '51.67,-0.61'}])[:8]
            atomic_json(root / 'plan.json', plan)
            atomic_json(root / 'runtime.json', {'phase': 'prepared', 'completedQueries': 0, 'tactics': {},
                                               'seenOwners': [], 'errors': [], 'baselineOwnersCaptured': True})
            job = {'originalOwners': [], 'keywordIndexPath': str(root / 'index.sqlite'), 'towns': [],
                   'requestIntervalSeconds': 0, 'originalBankExposureCents': 9901}
            codes = iter([40101] * 5 + [40102] * 2 + [20000])
            def provider(cache, lane, payload, maximum_cents):
                key = request_key(lane, payload);cache.reserve(key, lane, payload, maximum_cents)
                response = self.response(next(codes));response['tasks'][0]['id'] = key
                cache.settle(key, response);return response
            with patch.object(discovery, 'load_job', return_value=(job, float('inf'))), \
                    patch.object(discovery, 'choose_query', side_effect=[*plan, None]), \
                    patch.object(discovery, 'provider_request', side_effect=provider) as submitted, \
                    patch.object(discovery.SearchIndex, 'sync_run'), patch.object(discovery, 'wake_qualification'):
                discovery.run(root)
            state = json.loads((root / 'runtime.json').read_text())
            self.assertEqual(submitted.call_count, 8)
            self.assertEqual(state['completedQueries'], 8)
            self.assertEqual(state['phase'], 'search_plan_exhausted')
            self.assertEqual(state['outcomes'], {'source_error': 5, 'empty': 2, 'success': 1})
            cache = PaidCache(root, 5000, 0)
            try:
                self.assertEqual(cache.usage()['succeeded'], 1)
                self.assertEqual(cache.usage()['failed'], 7)
                self.assertEqual(cache.usage()['uncertainCents'], 0)
            finally:
                cache.db.close()

    def test_drained_profile_replacement_preserves_deadline_and_rejects_live_or_nonempty_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'profiles';root.mkdir()
            authorization = Path(directory) / 'approval.json'
            auth = dict(self.auth(), previousProfileRoot=str(root));atomic_json(authorization, auth)
            atomic_json(root / 'job.json', {'deadlineUnix': 123})
            atomic_json(root / 'state.json', {'queue': [], 'phase': 'running'})
            atomic_json(root / 'status.json', {'queue': 0, 'phase': 'awaiting_sources'})
            before = (root / 'job.json').read_bytes()
            with patch('prepare_recovery.process_live', return_value=True), self.assertRaises(ValueError):
                seal_drained_profile(root, authorization)
            with patch('prepare_recovery.process_live', return_value=False):
                atomic_json(root / 'state.json', {'queue': ['https://example.org/'], 'phase': 'running'})
                with self.assertRaises(ValueError):seal_drained_profile(root, authorization)
                atomic_json(root / 'state.json', {'queue': [], 'phase': 'running'})
                seal_drained_profile(root, authorization)
            self.assertEqual(before, (root / 'job.json').read_bytes())
            self.assertTrue(json.loads((root / 'status.json').read_text())['endedEarly'])

    def test_recovery_loads_the_existing_bank_without_creating_a_fresh_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory) / 'bank';bank.mkdir();root = Path(directory) / 'recovery';root.mkdir()
            start = datetime.datetime.now(datetime.timezone.utc)
            old_auth = {'instruction': discovery.INSTRUCTION, 'requestedAt': start.isoformat(),
                'deadline': (start + datetime.timedelta(hours=12)).isoformat(), 'additionalDiscoveryCents': 5000,
                'previousDiscoveryCapCents': 10000, 'combinedDiscoveryCapCents': 15000, 'originalBankExposureCents': 9901}
            for name, value in [('authorization.json', old_auth), ('plan.json', []), ('discovery-scope.json', {})]:
                atomic_json(bank / name, value)
            original = {'budgetCents': 5000, 'priorExposureCents': 0, 'originalBankExposureCents': 9901,
                'combinedCapCents': 15000, 'maximumSdkTurns': 0, 'keywordIndexPath': '/saved/index.sqlite',
                'originalOwners': [], 'towns': [], 'authorizedStartedAt': old_auth['requestedAt'],
                'authorizedDeadline': old_auth['deadline'], 'authorizationSha256': file_hash(bank / 'authorization.json'),
                'planSha256': file_hash(bank / 'plan.json'), 'scopeSha256': file_hash(bank / 'discovery-scope.json')}
            atomic_json(bank / 'manifest.json', original)
            auth = dict(self.auth(), bankRoot=str(bank), bankManifestSha256=file_hash(bank / 'manifest.json'))
            for name, value in [('authorization.json', auth), ('plan.json', []), ('discovery-scope.json', {})]:
                atomic_json(root / name, value)
            recovery = dict(original, recoveryBankRoot=str(bank), monitorIntervalSeconds=1800,
                authorizedStartedAt=auth['requestedAt'], authorizedDeadline=auth['deadline'],
                authorizationSha256=file_hash(root / 'authorization.json'))
            atomic_json(root / 'manifest.json', recovery)
            loaded, _ = discovery.load_job(root)
            self.assertEqual(loaded['recoveryBankRoot'], str(bank))
            self.assertFalse((root / 'paid.sqlite').exists())
            atomic_json(root / 'manifest.json', dict(recovery, budgetCents=10000))
            with self.assertRaises(ValueError):discovery.load_job(root)
            self.assertEqual(json.loads((bank / 'manifest.json').read_text()), original)

    def test_monitor_keeps_reporting_account_stops_until_deadline_without_paid_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root / 'runtime.json', {'phase': 'account_blocked',
                'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()})
            job = {'qualificationRoot': str(root / 'qualification'), 'authorizedDeadline': self.auth()['deadline']}
            with patch.object(discovery, 'load_job', return_value=(job, float('inf'))), \
                    patch.object(discovery, 'process_live', return_value=False), \
                    patch.object(discovery.subprocess, 'run') as commands:
                discovery.watch(root)
                commands.assert_not_called()
            self.assertTrue(json.loads((root / 'monitor.json').read_text())['terminal'])


if __name__ == '__main__':
    unittest.main()
