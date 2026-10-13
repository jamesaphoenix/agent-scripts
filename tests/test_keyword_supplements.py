import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import PaidCache, atomic_json, request_key
from keyword_expansion import SearchIndex, keyword_plan, search_identity
from keyword_supplements import recursive_query_supplements
import run as runner

TOWNS = [{'name': 'Amersham', 'coordinate': '51.674,-0.607'}]


class RecursiveSupplementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.index = SearchIndex(self.root / 'keywords.sqlite')
        self.addCleanup(self.index.close)
        self.index.configure(TOWNS)
        self.job = {'keywordIndexPath': str(self.index.path), 'towns': TOWNS}
        self.parent = keyword_plan(TOWNS, ['11+ tutor'])[0]
        self.submit(self.parent, 'parent')
        self.parent_key = search_identity(self.parent['lane'], self.parent['payload'])

    def submit(self, item, name, status='succeeded'):
        root = self.root / name
        cache = PaidCache(root, 200, 0)
        try:
            cache.reserve(item['key'], item['lane'], item['payload'], 100)
            if status in ('uncertain', 'submitting'):
                if status == 'uncertain': cache.mark_uncertain(item['key'], 'No verified receipt')
            else:
                code = 20000 if status == 'succeeded' else 40102
                cache.settle(item['key'], {'status_code': 20000, 'cost': .002,
                    'tasks': [{'id': name, 'cost': .002, 'status_code': code,
                               'result': [{'items': []}]}]})
        finally:
            cache.db.close()
        atomic_json(root / 'plan.json', [item])
        self.index.sync_run(root, TOWNS)

    def propose(self, phrase='home education tutor', parent=None):
        self.index.propose(parent or self.parent_key,
            [{'phrase': phrase, 'reason': 'Find another advertised tutoring service'}], 'test')

    def load(self, **kwargs):
        return recursive_query_supplements(self.job, **kwargs)

    def test_new_children_become_live_queries_without_repreparing_the_plan(self):
        self.assertEqual(self.load()['queries'], [])
        self.propose()
        result = self.load()
        self.assertEqual(len(result['queries']), 2)
        self.assertEqual({q['lane'] for q in result['queries']}, {'maps', 'organic'})
        for item in result['queries']:
            self.assertEqual(item['parentQueryKey'], self.parent_key)
            self.assertEqual(item['rootQueryKey'], self.parent_key)
            self.assertEqual(item['generation'], 1)
            self.assertEqual(item['tactic'], 'recursive_keywords')
            self.assertEqual(item['key'], request_key(item['lane'], item['payload']))
            self.assertEqual(item['payload']['keyword'], 'home education tutor Amersham')

    def test_read_only_bridge_preserves_index_ledger_and_frozen_files(self):
        self.propose()
        cache = PaidCache(self.root / 'live', 200, 0)
        self.addCleanup(cache.db.close)
        before = cache.usage()
        plan = json.dumps([self.parent], sort_keys=True)
        atomic_json(self.root / 'live/plan.json', [self.parent])
        tables = ['research_config', 'search_queries', 'query_edges', 'search_observations']
        snapshot = {t: list(map(tuple, self.index.db.execute('SELECT * FROM ' + t))) for t in tables}
        result = self.load(excluded_keys={q['key'] for q in self.index.pending_plan()})
        self.assertEqual(result['queries'], [])
        self.assertEqual(cache.usage(), before)
        self.assertEqual(json.dumps(json.loads((self.root / 'live/plan.json').read_text()), sort_keys=True), plan)
        self.assertEqual(snapshot, {t: list(map(tuple, self.index.db.execute('SELECT * FROM ' + t))) for t in tables})

    def test_all_observed_outcomes_block_repayment_but_current_queries_keep_attribution(self):
        for outcome in ('succeeded', 'failed', 'submitting', 'uncertain'):
            with self.subTest(outcome=outcome):
                self.propose(outcome + ' tutor')
                child = next(q for q in self.index.pending_plan() if q['payload']['keyword'] == outcome + ' tutor Amersham')
                self.submit(child, outcome, outcome)
                self.assertNotIn(child['key'], {q['key'] for q in self.load()['queries']})
                self.assertIn(child['key'], {q['key'] for q in self.load(current_keys={child['key']})['queries']})

    def test_bad_child_is_held_without_losing_other_children(self):
        self.propose()
        child = self.index.db.execute('SELECT * FROM search_queries WHERE generation=1 LIMIT 1').fetchone()
        payload = json.loads(child['payload'])
        payload['depth'] = 1000
        with self.index.db:
            self.index.db.execute('UPDATE search_queries SET payload=? WHERE query_key=?', (json.dumps(payload), child['query_key']))
        result = self.load()
        self.assertEqual(len(result['queries']), 1)
        self.assertEqual(result['heldQueries'], 1)

    def test_changed_geography_or_missing_configuration_fails_closed(self):
        self.propose()
        changed = dict(self.job, towns=[{'name': 'London', 'coordinate': '51.5,-0.1'}])
        with self.assertRaises(ValueError): recursive_query_supplements(changed)
        with self.index.db: self.index.db.execute('DELETE FROM research_config')
        with self.assertRaises(ValueError): self.load()

    def test_unverified_parent_and_broken_generation_or_root_cannot_expand(self):
        self.propose()
        with self.index.db: self.index.db.execute('UPDATE search_observations SET receipt_verified=0')
        self.assertEqual(self.load()['queries'], [])
        with self.index.db:
            self.index.db.execute('UPDATE search_observations SET receipt_verified=1')
            self.index.db.execute('UPDATE search_queries SET generation=3 WHERE generation=1')
        self.assertEqual(self.load()['queries'], [])

    def test_grandchildren_need_verified_parent_and_retain_the_first_root(self):
        self.propose()
        child = next(q for q in self.load()['queries'] if q['lane'] == 'organic')
        self.submit(child, 'child')
        self.propose('dyscalculia specialist tutor', child['queryKey'])
        descendants = [q for q in self.load()['queries'] if q['generation'] == 2]
        self.assertEqual(len(descendants), 2)
        self.assertTrue(all(q['rootQueryKey'] == self.parent_key for q in descendants))
        with self.index.db:
            self.index.db.execute('UPDATE search_queries SET root_key=? WHERE generation=2', ('f' * 64,))
        self.assertFalse(any(q['generation'] == 2 for q in self.load()['queries']))

    def test_unbounded_limits_and_overfull_parent_are_rejected(self):
        self.propose()
        for limit in (True, 0, 5001):
            with self.assertRaises(ValueError): self.load(limit=limit)
        config = self.index.configuration()
        config['maxChildren'] = 1
        with self.index.db:
            self.index.db.execute('UPDATE research_config SET value=?', (json.dumps(config),))
        self.assertEqual(self.load()['queries'], [])

    def test_live_refresh_reopens_an_exhausted_lane_without_changing_budget(self):
        cache = PaidCache(self.root / 'live', 200, 0)
        self.addCleanup(cache.db.close)
        job = dict(self.job, adaptiveTactics=True)
        state = {'errors': [], 'completedQueries': [], 'nextDue': {'maps': 2000, 'organic': 2000}}
        before = cache.usage()
        with mock.patch.object(runner.time, 'time', return_value=1000):
            runner.refresh_recursive_queries(job, cache, state, [], 2000)
            self.assertEqual(state['recursiveQueries'], [])
        self.propose()
        with mock.patch.object(runner.time, 'time', return_value=1061):
            runner.refresh_recursive_queries(job, cache, state, [], 2000)
        self.assertEqual(len(state['recursiveQueries']), 2)
        self.assertEqual(state['nextDue'], {'maps': 1061, 'organic': 1061})
        self.assertEqual(cache.usage(), before)
        with mock.patch.object(runner.time, 'time', return_value=1122):
            state['nextDue'] = {'maps': 1181, 'organic': 1301}
            runner.refresh_recursive_queries(job, cache, state, [], 2000)
        self.assertEqual(state['nextDue'], {'maps': 1181, 'organic': 1301})

    def test_bad_index_holds_supplements_while_base_queries_and_deadline_survive(self):
        cache = PaidCache(self.root / 'live', 200, 0)
        self.addCleanup(cache.db.close)
        state = {'errors': [], 'completedQueries': [], 'nextDue': {'maps': 2000, 'organic': 2000},
                 'recursiveQueries': [{'payload': {'keyword': 'stale tutor London'}}]}
        job = dict(self.job, adaptiveTactics=True, keywordIndexPath=str(self.root / 'absent.sqlite'))
        before = cache.usage()
        with mock.patch.object(runner.time, 'time', return_value=2000):
            runner.refresh_recursive_queries(job, cache, state, [self.parent], 2000)
        self.assertEqual(state['recursiveQueries'], [])
        self.assertEqual(state['nextDue'], {'maps': 2000, 'organic': 2000})
        self.assertEqual(cache.usage(), before)
        self.assertEqual(state['errors'][-1]['lane'], 'recursive_keywords')


if __name__ == '__main__':
    unittest.main()
