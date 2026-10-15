import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import incremental_discovery as discovery
from discovery import PaidCache, atomic_json, file_hash
from keyword_expansion import SearchIndex, search_identity


class IncrementalDiscoveryTests(unittest.TestCase):
    def auth(self):
        start = datetime.datetime.now(datetime.timezone.utc)
        return {'instruction': discovery.INSTRUCTION, 'requestedAt': start.isoformat(),
                'deadline': (start + datetime.timedelta(hours=12)).isoformat(),
                'additionalDiscoveryCents': 5000, 'previousDiscoveryCapCents': 10000,
                'combinedDiscoveryCapCents': 15000, 'originalBankExposureCents': 9901}

    def test_only_explicit_additive_budget_and_exact_window_are_admitted(self):
        auth = self.auth()
        discovery.validate_authorization(auth)
        for key, value in [('additionalDiscoveryCents', 5001), ('combinedDiscoveryCapCents', 16000),
                           ('instruction', 'keep going'), ('deadline', auth['requestedAt'])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                discovery.validate_authorization(dict(auth, **{key: value}))

    def test_plans_keep_geography_subjects_and_semantic_identities(self):
        towns = [{'name': 'Amersham', 'coordinate': '51.67,-0.61'}]
        plan = discovery.query_plan(towns)
        self.assertEqual(len(plan), len({search_identity(q['lane'], q['payload']) for q in plan}))
        self.assertEqual({q['town'] for q in plan}, {'Amersham'})
        self.assertEqual({q['lane'] for q in plan}, {'maps', 'organic'})
        self.assertTrue(any('site:' in q['payload']['keyword'] for q in plan))
        for q in plan:
            self.assertIn('Amersham', q['payload']['keyword'])
            self.assertNotIn('music tutor', q['payload']['keyword'])
            if q['lane'] == 'maps':
                self.assertEqual(q['payload']['depth'], 100)
                self.assertNotIn('site:', q['payload']['keyword'])

    def test_known_and_uncertain_searches_are_not_selected_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = PaidCache(root / 'bank', 5000, 0)
            index = SearchIndex(root / 'index.sqlite')
            try:
                plan = discovery.query_plan([{'name': 'Amersham', 'coordinate': '51.67,-0.61'}])[:2]
                first = plan[0]
                cache.reserve(first['key'], first['lane'], first['payload'], 2)
                cache.mark_uncertain(first['key'], 'Unverified paid response')
                picked = discovery.choose_query(plan, cache, index, {'completedQueries': 0, 'tactics': {}})
                self.assertEqual(picked['key'], plan[1]['key'])
                self.assertEqual(cache.usage()['uncertainCents'], 2)
            finally:
                cache.db.close()
                index.close()

    def test_additional_bank_does_not_release_original_hold_or_expand_old_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = PaidCache(root / 'old', 1000, 9000)
            new = PaidCache(root / 'new', 5000, 0)
            try:
                old.reserve('held', 'maps', {}, 100)
                old.mark_uncertain('held', 'Unknown original submission')
                before = old.usage()
                new.import_unsettled('maps', {'keyword': 'tutor'}, 'Original bank retains reservation')
                self.assertEqual(old.usage(), before)
                self.assertEqual(new.usage()['remainingCents'], 5000)
                self.assertEqual(new.usage()['importedUnsettledRequests'], 1)
                self.assertLessEqual(before['sharedExposureCents'] + 5000, 15000)
                with self.assertRaises(ValueError):
                    PaidCache(root / 'old', 6000, 9000)
            finally:
                old.db.close()
                new.db.close()

    def test_restart_validates_authorization_deadline_and_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = self.auth()
            for name, value in [('authorization.json', auth), ('plan.json', []), ('discovery-scope.json', {})]:
                atomic_json(root / name, value)
            job = {'authorizationSha256': file_hash(root / 'authorization.json'),
                   'planSha256': file_hash(root / 'plan.json'), 'scopeSha256': file_hash(root / 'discovery-scope.json'),
                   'budgetCents': 5000, 'priorExposureCents': 0, 'combinedCapCents': 15000,
                   'originalBankExposureCents': 9901, 'authorizedDeadline': auth['deadline'],
                   'authorizedStartedAt': auth['requestedAt'], 'maximumSdkTurns': 0}
            atomic_json(root / 'manifest.json', job)
            discovery.load_job(root)
            for change in [{'authorizedDeadline': auth['requestedAt']}, {'budgetCents': 5100}, {'maximumSdkTurns': 1}]:
                atomic_json(root / 'manifest.json', dict(job, **change))
                with self.assertRaises(ValueError):
                    discovery.load_job(root)
            atomic_json(root / 'manifest.json', job)
            atomic_json(root / 'plan.json', ['changed'])
            with self.assertRaises(ValueError):
                discovery.load_job(root)

    def test_profile_seeding_keeps_fifteen_per_search_and_nominations_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = {'towns': [{'name': 'Amersham'}], 'profileRoot': str(root / 'profiles')}
            item = {'key': 'a' * 64, 'tactic': 'public_profiles'}
            urls = [(f'https://example.com/tutor-{i}', {'title': 'Maths tutor Amersham'}) for i in range(30)]
            state = {'tactics': {}}
            with patch.object(discovery, 'upstream_results', return_value=iter(urls)), \
                    patch.object(discovery, 'admitted_url', side_effect=lambda value: value), \
                    patch.object(discovery, 'seed_batch') as seed:
                discovery.observe_results(root, job, item, {'tasks': []}, state, set())
                self.assertEqual(len(seed.call_args.args[1]), 15)
            observation = json.loads((root / 'observations' / ('a' * 64 + '.json')).read_text())
            self.assertFalse(observation['outreachEligible'])
            self.assertIn('not verified', observation['qualification'])

    def test_expired_window_never_reads_credentials_or_dispatches(self):
        with patch.object(discovery, 'load_job', return_value=({}, 1)), \
                patch.object(discovery, 'provider_request') as provider:
            discovery.run(Path('/unused'))
            provider.assert_not_called()

    def test_preparation_preserves_original_bank_and_cannot_mint_a_second_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory)
            old = history / 'tutor-lead-pathways-old'
            qualification = history / 'previous-qualification'
            old.mkdir()
            qualification.mkdir()
            towns = [{'name': 'Amersham', 'coordinate': '51.67,-0.61'}]
            scope = qualification / 'discovery-scope.json'
            atomic_json(scope, {})
            atomic_json(qualification / 'job.json', {'towns': ['Amersham'], 'scope': str(scope)})
            atomic_json(qualification / 'exports/audited-subject-contacts.json', [])
            atomic_json(old / 'manifest.json', {})
            atomic_json(old / 'towns.json', towns)
            original = (old / 'manifest.json').read_bytes()
            authorization = history / 'request.json'
            atomic_json(authorization, self.auth())
            payload = {'keyword': 'private tutor Amersham'}
            archives = [(old, [('maps', payload, None, 'Unknown response')])]
            root = history / 'tutor-incremental-test'
            with patch.object(discovery, 'archive_history', return_value=(9901, archives)), \
                    patch.object(discovery, 'process_live', return_value=False):
                result = discovery.prepare(root, authorization, history, qualification,
                    history / 'new-qualification', history / 'profiles')
                self.assertEqual(result['priorExposureRetainedCents'], 9901)
                self.assertGreater(result['queries'], 0)
                with self.assertRaisesRegex(ValueError, 'already has an owner'):
                    discovery.prepare(history / 'tutor-incremental-second', authorization, history,
                        qualification, history / 'other-qualification', history / 'other-profiles')
            self.assertEqual(original, (old / 'manifest.json').read_bytes())
            cache = PaidCache(root, 5000, 0)
            try:
                self.assertEqual(cache.usage()['remainingCents'], 5000)
                self.assertEqual(cache.usage()['importedUnsettledRequests'], 1)
                self.assertEqual(cache.get(discovery.request_key('maps', payload))['status'], 'uncertain')
            finally:
                cache.db.close()


if __name__ == '__main__':
    unittest.main()
