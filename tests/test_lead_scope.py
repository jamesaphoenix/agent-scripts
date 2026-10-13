"""Campaign subject filters must use a tutor's own advertised services."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from lead_scope import load_scope, matching_subjects, partition_contacts, scope_prompt, inherit_scope, query_priority


SCOPE = {'schemaVersion': 1, 'subjects': ['english', 'maths', 'verbal_reasoning', 'non_verbal_reasoning'],
         'includeElevenPlus': True, 'includePrimaryTutors': True, 'excludeMusicOnly': True,
         'authorization': {'instruction': 'English / maths / verbal reasoning / non verbal reasoning; no music teachers',
                           'recordedAt': '2026-10-13T19:40:00+00:00'}}


class LeadScopeTests(unittest.TestCase):
    def test_specific_subject_queries_precede_generic_tutors_and_hold_other_subjects(self):
        for keyword, expected in [('11%2B tutor Amersham', 0), ('maths tutor Aylesbury', 0),
                                  ('non verbal reasoning tutor Marlow', 0), ('tutor Amersham', 1),
                                  ('piano tutor Amersham', 2), ('science tutor Amersham', 2)]:
            self.assertEqual(query_priority(keyword, SCOPE), expected)
        from run import next_query
        from unittest.mock import Mock
        cache = Mock()
        cache.get.return_value = None
        plan = [{'lane': 'organic', 'key': str(i), 'payload': {'keyword': word}}
                for i, word in enumerate(['piano tutor Amersham', 'tutor Amersham', 'maths tutor Amersham'])]
        state = {'completedQueries': [], 'subjectScope': SCOPE}
        self.assertEqual(next_query('organic', plan, cache, state)['key'], '2')
        state['completedQueries'] = ['2', '1']
        self.assertIsNone(next_query('organic', plan, cache, state))

    def test_subject_claims_are_specific_and_do_not_borrow_names_or_urls(self):
        cases = [(['English Literature', 'Piano'], ['english']), (['Numeracy', 'Algebra'], ['maths']),
                 (['Verbal Reasoning'], ['verbal_reasoning']),
                 (['Non-verbal reasoning'], ['non_verbal_reasoning']),
                 (['NVR', 'VR'], ['verbal_reasoning', 'non_verbal_reasoning']),
                 (['Phonics and literacy'], ['english']), (['Eleven Plus lessons'], ['eleven_plus']),
                 (['11+ (secondary)'], []), (['GCSE Music', 'Piano'], []),
                 (['Science', 'GCSE'], []), (['Non-verbal communication'], []), (['Conversation'], [])]
        for services, expected in cases:
            with self.subTest(services=services):
                row = {'services': services, 'name': 'Maths English 11+ Tutor', 'website': 'https://maths.co.uk'}
                self.assertEqual(matching_subjects(row, SCOPE), expected)

    def test_primary_fallback_does_not_turn_music_into_a_primary_tutor(self):
        self.assertEqual(matching_subjects({'services': ['Primary up to 11 years']}, SCOPE), ['primary'])
        self.assertEqual(matching_subjects({'services': ['Primary school piano lessons']}, SCOPE), [])
        self.assertEqual(matching_subjects({'services': ['Primary school', 'Piano']}, SCOPE), [])
        self.assertEqual(matching_subjects({'services': ['Primary school', 'Maths', 'Piano']}, SCOPE), ['maths'])

    def test_new_crawl_roots_and_listing_cards_do_not_keep_music_only_seeds(self):
        from run import prioritise_seed_records
        from directory_profiles import listing_profile_urls
        rows = [{'name': 'Jane Piano', 'website': 'https://piano.example'},
                {'name': 'Jane Maths', 'website': 'https://maths.example'},
                {'name': 'Jane Tutoring', 'website': 'https://tutor.example'}]
        self.assertEqual([r['name'] for r in prioritise_seed_records(rows, {'discoveryScope': SCOPE})],
                         ['Jane Maths', 'Jane Tutoring'])
        cards = [dict(name='Jane Example', platform='tutorextra.co.uk', localityClaim='Amersham', services=services,
                      sourceUrls=[f'https://tutorextra.co.uk/profile/jane/{index}-42'], evidenceKind='directory_listing')
                 for index, services in enumerate([['Music'], ['Maths', 'Piano'], ['Tutor', 'Teacher']], 1)]
        self.assertEqual(listing_profile_urls(cards, 'tutorextra.co.uk', SCOPE),
                         ['https://tutorextra.co.uk/profile/jane/2-42', 'https://tutorextra.co.uk/profile/jane/3-42'])

    def test_partition_keeps_mixed_subject_tutors_and_preserves_raw_evidence(self):
        rows = [{'name': 'A', 'services': ['Maths', 'Music'], 'outreachEligible': False},
                {'name': 'B', 'services': ['Singing', 'GCSE'], 'outreachEligible': False},
                {'name': 'C', 'services': ['Chemistry'], 'outreachEligible': False},
                {'name': 'D', 'services': [], 'outreachEligible': False}]
        before = json.dumps(rows)
        accepted, held, summary = partition_contacts(rows, SCOPE)
        self.assertEqual([r['name'] for r in accepted], ['A'])
        self.assertEqual([r['scopeReason'] for r in held], ['music_only', 'other_subject', 'subject_unconfirmed'])
        self.assertEqual(summary['matchingContacts'], 1)
        self.assertEqual(summary['excludedContacts'], 3)
        self.assertEqual(json.dumps(rows), before)
        self.assertFalse(accepted[0]['outreachEligible'])

    def test_missing_scope_preserves_generic_campaigns_and_invalid_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(load_scope(root))
            for value in [dict(SCOPE, subjects=['piano']), dict(SCOPE, excludeMusicOnly=False),
                          dict(SCOPE, includeElevenPlus='yes'), dict(SCOPE, schemaVersion=2)]:
                (root / 'discovery-scope.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    load_scope(root)
            (root / 'discovery-scope.json').write_text(' ' * 8193)
            with self.assertRaises(ValueError):
                load_scope(root)

    def test_extension_inherits_the_validated_policy_without_rewriting_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent, child = Path(temporary) / 'parent', Path(temporary) / 'child'
            parent.mkdir(); child.mkdir()
            path = parent / 'discovery-scope.json'
            path.write_text(json.dumps(SCOPE))
            before = path.read_bytes()
            proof = inherit_scope(parent, child)
            self.assertEqual((child / path.name).read_bytes(), before)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(proof['sha256']), 64)
            self.assertIn('Exclude music-only', scope_prompt(load_scope(child)))

    def test_campaign_export_filters_all_sources_but_retains_the_broad_audit(self):
        from campaign_watch import aggregate_contacts
        from discovery import atomic_json
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root / 'baseline/contacts.json', [])
            atomic_json(root / 'discovery-scope.json', SCOPE)
            rows = [{'name': 'Maths Jane', 'canonicalId': 'a', 'services': ['Maths'], 'outreachEligible': False},
                    {'name': 'Music John', 'canonicalId': 'b', 'services': ['Piano'], 'outreachEligible': False}]
            atomic_json(root / 'profile-audit/this-stage-delta/new.json', rows)
            report = aggregate_contacts(root, {'outputDirectory': str(root / 'profile-audit')})
            self.assertEqual(report['subjectScope']['matchingContacts'], 1)
            self.assertEqual(len(json.loads((root / 'exports/additional-contacts-all-subjects.json').read_text())), 2)
            accepted = json.loads((root / 'exports/additional-contacts.json').read_text())
            self.assertEqual([r['name'] for r in accepted], ['Maths Jane'])
            self.assertFalse(accepted[0]['outreachEligible'])


if __name__ == '__main__':
    unittest.main()
