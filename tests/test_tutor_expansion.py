import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'
sys.path.insert(0, str(HERE))


class ExpansionTests(unittest.TestCase):
    def test_receipts_survive_launcher_working_directory_changes(self):
        from discovery import PaidCache
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            envelope = {'status_code': 20000, 'cost': 0.002, 'tasks': [
                {'id': 'task', 'cost': 0.002, 'status_code': 20000, 'result': []}]}
            cache.import_receipt('organic', {'keyword': 'cached'}, envelope)
            row = cache.db.execute('SELECT * FROM requests').fetchone()
            cache.db.execute('UPDATE requests SET receipt=?', ('state/old/receipts/' + row['key'] + '.json',))
            cache.db.commit()
            row = cache.db.execute('SELECT * FROM requests').fetchone()
            self.assertEqual(cache.receipt(row), envelope)

    def test_plan_has_distinct_tactics_and_no_duplicate_paid_identities(self):
        from tactics import build_plan
        plan = build_plan([{'name': 'Milton Keynes', 'coordinate': '52.040,-0.759'}])
        self.assertEqual(len({r['key'] for r in plan}), len(plan))
        tactics = {r['tactic'] for r in plan}
        self.assertTrue({'maps_subject', 'public_profiles', 'own_sites', 'community'} <= tactics)
        self.assertTrue(any('site:superprof.co.uk' in r['payload'].get('keyword', '') for r in plan))
        self.assertTrue(any('11%2B' in r['payload'].get('keyword', '') for r in plan))
        self.assertTrue(all(r['payload'].get('depth', 0) <= 100 for r in plan))

    def test_manifest_intervals_do_not_change_historical_jobs(self):
        from run import request_intervals, INTERVALS
        self.assertEqual(request_intervals({}), INTERVALS)
        custom = {'maps': 15, 'business': 30, 'organic': 15, 'directories': 15}
        self.assertEqual(request_intervals({'requestIntervals': custom}), custom)
        for value in [dict(custom, maps=0), dict(custom, maps=True), {'maps': 15}]:
            with self.assertRaises(ValueError):
                request_intervals({'requestIntervals': value})

    def test_entire_remaining_budget_has_one_shared_cap(self):
        from discovery import PaidCache
        with tempfile.TemporaryDirectory() as d:
            cache = PaidCache(Path(d), 8874, 1126)
            self.addCleanup(cache.db.close)
            for n in range(88):
                self.assertTrue(cache.reserve(str(n), 'maps', {}, 100))
            self.assertFalse(cache.reserve('two', 'organic', {}, 100))
            self.assertLessEqual(cache.usage()['sharedExposureCents'], 10000)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                PaidCache(Path(d), 8875, 1126)

    def test_metrics_exclude_inherited_urls_and_distinguish_empty_results(self):
        from discovery import PaidCache, request_key
        from expansion_metrics import metrics
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            def reply(task, urls):
                return {'status_code': 20000, 'cost': 0.002, 'tasks': [{'id': task, 'cost': 0.002,
                    'status_code': 20000, 'result': [{'items': [{'type': 'organic', 'url': u} for u in urls]}]}]}
            cache.import_receipt('organic', {'keyword': 'old'}, reply('old-task', ['https://old.co.uk/a']))
            payload = {'keyword': 'new'}
            key = request_key('organic', payload)
            cache.reserve(key, 'organic', payload, 100)
            cache.settle(key, reply('new-task', ['https://old.co.uk/a', 'https://new.co.uk/person']))
            result = metrics(root, cache, [{'key': key, 'tactic': 'public_profiles'}])
            self.assertEqual(result['tactics']['public_profiles']['newResultUrls'], 1)
            self.assertEqual(result['tactics']['public_profiles']['successfulRequests'], 1)
            self.assertFalse(result['urlCountsAreVerifiedTutors'])

    def test_tactic_feedback_distinguishes_classified_local_tutors_from_new_unqualified_urls(self):
        from discovery import PaidCache, request_key, atomic_json
        from expansion_metrics import metrics
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            focus = root / 'focused'
            atomic_json(root / 'manifest.json', {'focusedRoots': [str(focus)]})
            url, junk = 'https://new-tutor.co.uk/', 'https://fitness.co.uk/'
            atomic_json(focus / 'research-candidates.json', [{'url': url, 'profileKey': None,
                'classification': {'kind': 'independent_tutor', 'kindProbability': .9, 'niche': .9, 'local': .9, 'outside': .01}}])
            cache = PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            payload = {'keyword': 'tutor Amersham'}
            key = request_key('organic', payload)
            cache.reserve(key, 'organic', payload, 100)
            cache.settle(key, {'status_code': 20000, 'cost': .002, 'tasks': [{'id': 'new', 'status_code': 20000,
                'cost': .002, 'result': [{'items': [{'url': url}, {'url': junk}]}]}]})
            item = metrics(root, cache, [{'key': key, 'tactic': 'own_sites'}])['tactics']['own_sites']
            self.assertEqual(item['newResultUrls'], 2)
            self.assertEqual(item['classifiedLocalTutorCandidates'], 1)
            self.assertEqual(item['auditedNewIdentitiesAttributed'], 0)

    def test_delta_does_not_count_new_adverts_or_name_collisions_as_new_people(self):
        from new_leads import classify_delta
        def row(key, name, members=None):
            return {'canonicalId': key, 'memberKeys': members or [key], 'name': name,
                    'localityClaim': 'Amersham', 'outreachEligible': False}
        old = [row('old', 'Jane Smith')]
        current = [row('renamed', 'Jane Smith', ['old', 'extra']), row('new', 'Jane Smith'), row('other', 'Jane Brown')]
        result = classify_delta(old, current)
        self.assertEqual([r['canonicalId'] for r in result['new']], ['other'])
        self.assertEqual(len(result['needsReview']), 1)
        self.assertEqual(len(result['known']), 1)

class ProfileBridgeTests(unittest.TestCase):
    def test_seed_bridge_only_admits_supported_public_paths_and_is_hash_bound(self):
        from discovery import PaidCache
        from profile_seeds import write_seed_batch
        from directory_profiles import import_seed_batches
        with tempfile.TemporaryDirectory() as d:
            root, target = Path(d) / 'paid', Path(d) / 'profiles'
            cache = PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            cache.import_receipt('organic', {'keyword': 'tutors'}, {'status_code': 20000, 'cost': 0.002,
                'tasks': [{'id': 'one', 'cost': 0.002, 'status_code': 20000, 'result': [{'items': [
                    {'url': 'https://www.uktutors.com/tutor/1234/jane-tutor'},
                    {'url': 'https://www.uktutors.com/login'}, {'url': 'https://evil.test/tutor/1'}]}]}]})
            result = write_seed_batch(root, target, cache)
            self.assertEqual(result['urls'], 1)
            state = {'seen': [], 'queue': []}
            import_seed_batches(target, state)
            self.assertEqual(state['queue'], ['https://www.uktutors.com/tutor/1234/jane-tutor'])
            self.assertEqual(len(state['importedSeedBatches']), 1)
            write_seed_batch(root, target, cache)
            import_seed_batches(target, state)
            self.assertEqual(len(state['queue']), 1)

    def test_empty_frontier_waits_for_paid_source_but_not_after_completion(self):
        from directory_profiles import source_still_producing
        from discovery import atomic_json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            job = {'seedSourceRun': str(root)}
            self.assertTrue(source_still_producing(job))
            atomic_json(root / 'runtime.json', {'phase': 'running'})
            self.assertTrue(source_still_producing(job))
            atomic_json(root / 'runtime.json', {'phase': 'complete'})
            self.assertFalse(source_still_producing(job))
            self.assertFalse(source_still_producing({}))

    def test_tutorhunt_requires_own_identity_location_and_subjects(self):
        from directory_profiles import profile_record, admitted_url, exclusion_reason
        url = 'https://www.tutorhunt.com/users/123456/'
        html = '<div id="profilenameinfo"><h1>Jane</h1><div id="profileloc">Amersham, Buckinghamshire</div></div><table class="profilesubjectstattable"><tr><td class="marksub">Maths</td></tr></table><div class="card"><h2>Information about Jane</h2>I offer GCSE maths tuition.</div>'
        row = profile_record(html, url)
        self.assertEqual(row['name'], 'Jane')
        self.assertEqual(row['services'], ['Maths'])
        self.assertIsNone(admitted_url('https://www.tutorhunt.com/message.asp?user=123456'))
        self.assertIsNone(profile_record(html.replace('Amersham, Buckinghamshire', ''), url))
        inactive = profile_record(html + '<div>Not available to contact</div>', url)
        self.assertTrue(exclusion_reason(inactive))

class TacticSchedulingTests(unittest.TestCase):
    def test_profile_sources_are_explored_before_repeating_one_platform(self):
        from run import next_query
        from discovery import PaidCache, request_key
        with tempfile.TemporaryDirectory() as d:
            cache = PaidCache(Path(d), 200, 0)
            self.addCleanup(cache.db.close)
            plan = []
            for site in ('superprof.co.uk', 'tutorhunt.com'):
                for town in ('Amersham', 'Aylesbury'):
                    payload = {'keyword': f'site:{site} "{town}" tutor'}
                    plan.append({'lane': 'directories', 'tactic': 'public_profiles', 'payload': payload,
                                 'key': request_key('directories', payload)})
            state = {'completedQueries': []}
            first = next_query('directories', plan, cache, state)
            state['completedQueries'].append(first['key'])
            second = next_query('directories', plan, cache, state)
            self.assertIn('tutorhunt.com', second['payload']['keyword'])

    def test_closed_source_queries_are_skipped_without_provider_calls(self):
        from run import next_query
        from discovery import PaidCache, request_key
        with tempfile.TemporaryDirectory() as d:
            cache = PaidCache(Path(d), 200, 0)
            self.addCleanup(cache.db.close)
            payload = {'keyword': 'site:firsttutors.com "Amersham" tutor'}
            plan = [{'lane':'directories', 'payload':payload, 'key':request_key('directories',payload)}]
            state = {'completedQueries': []}
            self.assertIsNone(next_query('directories', plan, cache, state))
            self.assertEqual(cache.usage()['exposureCents'], 0)
            self.assertEqual(len(state['skippedQueries']), 1)


if __name__ == '__main__':
    unittest.main()
