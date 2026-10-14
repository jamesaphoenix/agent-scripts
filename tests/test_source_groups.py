from pathlib import Path
import sys
import unittest
import tempfile

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from source_groups import tactic_group


class RecursivePhraseGroups(unittest.TestCase):
    def test_a_repeated_phrase_does_not_suppress_exploration_of_another_service(self):
        from discovery import PaidCache, request_key
        from run import next_query
        with tempfile.TemporaryDirectory() as tmp:
            cache = PaidCache(Path(tmp), 200, 0)
            self.addCleanup(cache.db.close)
            plan, completed = [], []
            for town in ('Amersham', 'Marlow', 'Aylesbury', 'Chesham'):
                item = dict(self.item('qualified primary tutor', town), lane='maps')
                item['key'] = request_key('maps', item['payload'])
                plan.append(item)
                if town != 'Chesham':
                    cache.reserve(item['key'], 'maps', item['payload'], 100)
                    cache.settle(item['key'], {'cost': .002, 'status_code': 20000,
                        'tasks': [{'id': item['key'], 'cost': .002, 'status_code': 20000, 'result': []}]})
                    completed.append(item['key'])
            alternative = dict(self.item('English literacy tutor'), lane='maps')
            alternative['key'] = request_key('maps', alternative['payload'])
            plan.append(alternative)
            observed = {'requests': 3, 'chargedCents': 3, 'newResultUrls': 0}
            state = {'completedQueries': completed, 'adaptiveTactics': True, 'tacticFeedback': {
                'recursive_keywords': observed, 'recursive_keywords:qualified primary tutor': observed}}
            self.assertEqual(next_query('maps', plan, cache, state), alternative)

    def item(self, phrase, town='Amersham'):
        return {'tactic': 'recursive_keywords', 'town': town,
                'payload': {'keyword': phrase + ' ' + town}}

    def test_each_service_phrase_has_one_counter_across_towns_and_lanes(self):
        self.assertEqual(tactic_group(self.item('qualified primary tutor')),
                         'recursive_keywords:qualified primary tutor')
        self.assertEqual(tactic_group(self.item('qualified primary tutor', 'Marlow')),
                         tactic_group(self.item('qualified primary tutor')))
        self.assertNotEqual(tactic_group(self.item('English literacy tutor')),
                            tactic_group(self.item('qualified primary tutor')))
        self.assertEqual(tactic_group(self.item('11%2B maths tutor')),
                         tactic_group(self.item('11+ maths tutor')))

    def test_missing_or_unmatched_geography_keeps_the_existing_group(self):
        for item in ({'tactic': 'recursive_keywords', 'payload': {'keyword': 'tutor Amersham'}},
                     {'tactic': 'recursive_keywords', 'town': 'Marlow', 'payload': {'keyword': 'tutor Amersham'}},
                     {'tactic': 'recursive_keywords', 'town': 'Amersham', 'payload': {'keyword': 'site:example.org tutor Amersham'}}):
            self.assertEqual(tactic_group(item), 'recursive_keywords')

    def test_existing_directory_and_plain_tactics_keep_their_identity(self):
        self.assertEqual(tactic_group({'tactic': 'public_profiles', 'payload': {'keyword': 'site:tutorhunt.com tutor Amersham'}}),
                         'tutorhunt.com')
        self.assertEqual(tactic_group({'tactic': 'subject_profiles:tutorhunt.com'}), 'subject_profiles:tutorhunt.com')
        self.assertEqual(tactic_group({'tactic': 'keyword_expansion'}), 'keyword_expansion')
