from pathlib import Path
import json
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import PaidCache
from keyword_expansion import SearchIndex, keyword_plan, search_identity
from qualify_inventory import create_database
from search_replenishment import prepare_backlog

TOWNS = [{'name': 'Amersham', 'coordinate': '51.674,-0.607'}]
SCOPE = {'subjects': ['english', 'maths'], 'includeElevenPlus': True,
         'includePrimaryTutors': True, 'excludeMusicOnly': True}


class SearchReplenishmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.index = SearchIndex(self.root / 'keywords.sqlite'); self.addCleanup(self.index.close)
        self.index.configure(TOWNS)
        self.db = create_database(self.root / 'candidates.sqlite'); self.addCleanup(self.db.close)
        self.job = {'towns': ['Amersham'], 'deadlineUnix': time.time() + 60}

    def observed(self, item, status):
        root = self.root / status
        cache = PaidCache(root, 200, 0)
        cache.reserve(item['key'], item['lane'], item['payload'], 100)
        if status == 'uncertain':
            cache.mark_uncertain(item['key'], 'Unverified response')
        elif status in ('succeeded', 'failed'):
            cache.settle(item['key'], {'status_code': 20000, 'cost': .002,
                'tasks': [{'id': 'receipt-' + status, 'cost': .002,
                    'status_code': 20000 if status == 'succeeded' else 40102, 'result': []}]})
        cache.db.close()
        self.index.sync_run(root, TOWNS)

    def backlog(self):
        return prepare_backlog(self.root, self.db, self.index.path, self.job, SCOPE, {})

    def test_scoped_searches_are_persisted_without_mutating_the_paid_search_index(self):
        self.index.add_queries(keyword_plan(TOWNS, ['11+ tutor', 'maths tutor', 'music tutor', 'biology tutor']))
        before = self.index.db.total_changes
        first = self.backlog(); second = self.backlog()
        self.assertEqual(first['pendingQueries'], 4)
        self.assertEqual(second['pendingQueries'], 4)
        self.assertEqual(self.db.execute('SELECT count(*) FROM qualification_search_backlog').fetchone()[0], 4)
        self.assertEqual(before, self.index.db.total_changes)
        export = json.loads((self.root / 'search-replenishment.json').read_text())
        self.assertTrue(export['requiresPaidWindowAuthorization'])
        self.assertEqual(export['paidSearchCalls'], 0)
        self.assertFalse(export['dispatchEnabled'])
        self.assertEqual(export['deadlineUnix'], self.job['deadlineUnix'])

    def test_all_prior_request_outcomes_block_replay_and_retire_backlog_entries(self):
        plan = keyword_plan(TOWNS, ['maths tutor', 'english tutor'])
        self.index.add_queries(plan)
        self.assertEqual(self.backlog()['pendingQueries'], 4)
        for item, status in zip(plan, ['succeeded', 'failed', 'submitting', 'uncertain']):
            self.observed(item, status)
        self.assertEqual(self.backlog()['pendingQueries'], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM qualification_search_backlog WHERE state='observed'").fetchone()[0], 4)

    def test_geography_payload_and_deadline_are_rechecked(self):
        self.index.add_queries(keyword_plan(TOWNS, ['maths tutor']))
        self.job['towns'] = ['London']
        with self.assertRaises(ValueError): self.backlog()
        self.job['towns'] = ['Amersham']
        with self.index.db:
            self.index.db.execute("UPDATE search_queries SET root_key='wrong-root'")
        self.assertEqual(self.backlog()['pendingQueries'], 0)
        self.job['deadlineUnix'] = time.time() - 1
        self.assertEqual(self.backlog()['state'], 'deadline')

    def test_recursive_queries_need_verified_parent_lineage(self):
        parent = keyword_plan(TOWNS, ['maths tutor'])[0]
        self.index.add_queries([parent]); self.observed(parent, 'succeeded')
        self.index.propose(search_identity(parent['lane'], parent['payload']),
            [{'phrase': 'KS2 maths tutor', 'reason': 'Primary maths tuition in the same town'}], 'test')
        self.assertEqual(self.backlog()['pendingQueries'], 2)
        with self.index.db:
            self.index.db.execute("UPDATE search_observations SET receipt_verified=0")
        self.assertEqual(self.backlog()['pendingQueries'], 0)

    def test_backlog_is_bounded_and_prefers_sources_with_recorded_tutor_yield(self):
        towns = [{'name': 'Amersham', 'coordinate': '51.674,-0.607'},
                 {'name': 'High Wycombe', 'coordinate': '51.629,-0.749'}]
        self.index.close()
        self.index = SearchIndex(self.root / 'other-keywords.sqlite'); self.addCleanup(self.index.close)
        self.index.configure(towns)
        self.job['towns'] = [t['name'] for t in towns]
        self.index.add_queries(keyword_plan(towns, [f'maths tutor service {i}' for i in range(60)]))
        result = prepare_backlog(self.root, self.db, self.index.path, self.job, SCOPE,
            {'search:maps:High Wycombe': .9, 'search:organic:Amersham': .1})
        self.assertEqual(result['pendingQueries'], 200)
        self.assertEqual(result['eligibleQueries'], 240)
        export = json.loads((self.root / 'search-replenishment.json').read_text())
        self.assertEqual(export['queries'][0]['town'], 'High Wycombe')
        self.assertEqual(export['queries'][0]['lane'], 'maps')


if __name__ == '__main__': unittest.main()
