import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from candidate_review import CachedDecisions, assess, candidate_urls, page_index, run
from decisions_classifier import DecisionsCache
from test_decisions_classifier import response


class CandidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'decisions.sqlite'
        self.calls = []
        self.evidence = {'url': 'https://example.org/', 'title': 'Private tutor',
                         'text': 'I offer maths tutoring in Amersham. My lessons focus on algebra, arithmetic and geometry for learners.'}

    def seed(self, reply=None):
        def transport(payload):
            self.calls.append(payload)
            return reply or response()
        cache = DecisionsCache(self.path, ['Amersham'], transport=transport)
        result = cache.classify(self.evidence)
        cache.close()
        return result

    def test_replay_checks_the_exact_request_without_writing_or_paying(self):
        self.seed()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with CachedDecisions(self.path) as cache:
            decision = cache.lookup(self.evidence)
            self.assertEqual(decision['kind'], 'independent_tutor')
            self.assertIsNone(cache.lookup(dict(self.evidence, url='https://other.example/')))
            self.assertIsNone(cache.lookup(dict(self.evidence, text='I teach music.')))
            with self.assertRaises(sqlite3.OperationalError):
                cache.db.execute('DELETE FROM decision_requests')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(len(self.calls), 1)

    def test_corrupt_payload_proof_and_uncertain_submission_stay_held(self):
        self.seed()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE decision_requests SET payload_sha256='wrong'")
        with CachedDecisions(self.path) as cache:
            with self.assertRaises(ValueError): cache.lookup(self.evidence)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE decision_requests SET state='uncertain'")
        with CachedDecisions(self.path) as cache:
            with self.assertRaises(ValueError): cache.lookup(self.evidence)
        self.assertEqual(len(self.calls), 1)

    def test_url_and_search_context_cannot_supply_missing_subject_or_locality(self):
        decision = self.seed()
        row = {'name': 'Maths tutor Amersham', 'website': self.evidence['url'],
               'sourceUrl': self.evidence['url'], 'advertisedAddress': 'Amersham'}
        page = dict(self.evidence, text='Welcome to my personal website.')
        result = assess(row, [{'evidence': page, 'decision': decision}], ['Amersham'], set())
        self.assertEqual(result['status'], 'locality_review')
        result = assess(row, [{'evidence': dict(page, text='I tutor piano in Amersham.'),
                               'decision': decision}], ['Amersham'], set())
        self.assertEqual(result['status'], 'subject_review')
        self.assertFalse(result['outreachEligible'])
        self.assertFalse(result['qualifiedContact'])

    def test_positive_model_decision_is_a_review_candidate_not_a_new_contact(self):
        decision = self.seed()
        row = {'name': 'Jane Tutor', 'website': self.evidence['url']}
        result = assess(row, [{'evidence': self.evidence, 'decision': decision}], ['Amersham'], set())
        self.assertEqual(result['status'], 'tutor_candidate')
        self.assertEqual(result['subjectHints'], ['maths'])
        self.assertFalse(result['qualifiedContact'])
        known = assess(row, [], ['Amersham'], {self.evidence['url']})
        self.assertEqual(known['status'], 'already_in_tutor_export')

    def test_cached_review_checks_long_pages_without_relaxing_music_or_contact_rules(self):
        decision = self.seed()
        row = {'name': 'Jane Tutor', 'website': self.evidence['url']}
        filler = 'Information about my individual lessons. ' * 100
        page = dict(self.evidence, text=filler + ' I offer maths tuition in Amersham.')
        result = assess(row, [{'evidence': page, 'decision': decision}], ['Amersham'], set())
        self.assertEqual(result['status'], 'tutor_candidate')
        self.assertEqual(result['subjectHints'], ['maths'])
        self.assertFalse(result['qualifiedContact'])
        page['text'] = 'Primary school lessons in Amersham. ' + filler + ' Piano tuition.'
        result = assess(row, [{'evidence': page, 'decision': decision}], ['Amersham'], set())
        self.assertEqual(result['status'], 'subject_review')
        self.assertFalse(result['outreachEligible'])

    def test_two_profiles_on_one_directory_never_share_a_decision(self):
        self.evidence['url'] = 'https://www.childcare.co.uk/profile/111'
        self.seed()
        with CachedDecisions(self.path) as cache:
            self.assertIsNone(cache.lookup(dict(self.evidence, url='https://www.childcare.co.uk/profile/222')))

    def test_owned_about_pages_can_supply_context_but_other_directory_profiles_cannot(self):
        row = {'website': 'https://mytutor.example/', 'siteEvidence': [
            {'url': 'https://mytutor.example/about'}, {'url': 'https://another.example/contact'}]}
        self.assertEqual(candidate_urls(row), ['https://mytutor.example/', 'https://mytutor.example/about'])
        row = {'website': 'https://www.childcare.co.uk/profile/111', 'siteEvidence': [
            {'url': 'https://www.childcare.co.uk/profile/222'}]}
        self.assertEqual(candidate_urls(row), ['https://www.childcare.co.uk/profile/111'])
        row = {'website': 'https://jane.wixsite.com/tutoring', 'siteEvidence': [
            {'url': 'https://jane.wixsite.com/tutoring/about'}, {'url': 'https://jane.wixsite.com/music/about'}]}
        self.assertEqual(candidate_urls(row), ['https://jane.wixsite.com/tutoring', 'https://jane.wixsite.com/tutoring/about'])

    def test_recorded_redirects_recover_context_without_transferring_profile_identity(self):
        root=Path(self.temp.name);folder=root/'crawls/one';folder.mkdir(parents=True)
        rows=[{'node':{'url':'http://mytutor.example/'},'evidence':dict(self.evidence,url='https://www.mytutor.example/')},
              {'node':{'url':'https://www.childcare.co.uk/profile/111'},'evidence':dict(self.evidence,url='https://www.childcare.co.uk/profile/222')}]
        (folder/'crawl.json').write_text(json.dumps({'results':rows}))
        index=page_index([], [root])
        self.assertEqual(index['http://mytutor.example/']['url'],'https://www.mytutor.example/')
        self.assertNotIn('https://www.childcare.co.uk/profile/111',index)
        self.assertNotIn('https://www.childcare.co.uk/profile/222',index)

    def test_conflicting_owned_pages_require_review(self):
        decision = self.seed()
        other = dict(decision, kind='institution')
        row = {'name': 'Tutor', 'website': self.evidence['url']}
        result = assess(row, [{'evidence': self.evidence, 'decision': decision},
                              {'evidence': self.evidence, 'decision': other}], ['Amersham'], set())
        self.assertEqual(result['status'], 'conflicting_decisions')

    def test_locality_beyond_the_model_context_does_not_qualify_a_page(self):
        decision = self.seed()
        page = dict(self.evidence, text='I tutor maths. ' + 'z' * 17000 + ' in Amersham.')
        result = assess({'name': 'Tutor', 'website': page['url']},
                        [{'evidence': page, 'decision': decision}], ['Amersham'], set())
        self.assertEqual(result['status'], 'locality_review')

    def test_inventory_replay_preserves_unknown_rows_and_does_not_queue_known_contacts(self):
        self.seed()
        root = Path(self.temp.name)
        candidates = root / 'candidates.json'
        candidates.write_text(json.dumps([
            {'name': 'Known', 'website': 'https://known.example/', 'status': 'research'},
            {'name': 'Unknown', 'website': 'https://unknown.example/', 'status': 'research'},
            {'name': 'Blocked', 'website': 'https://www.facebook.com/person', 'status': 'research'}]))
        contacts = root / 'contacts.json'
        contacts.write_text(json.dumps([{'sourceUrls': ['https://known.example/']}]))
        crawl = root / 'crawl-root/crawls/one/crawl.json'
        crawl.parent.mkdir(parents=True)
        crawl.write_text(json.dumps({'results': [
            {'node': {'url': url}, 'evidence': dict(self.evidence, url=url)}
            for url in ['https://known.example/', 'https://unknown.example/']]}))
        result = run(candidates, [], self.path, [contacts], root / 'review', crawl_roots=[root / 'crawl-root'])
        self.assertEqual(result['candidateRows'], 3)
        self.assertEqual(result['statuses'], {'already_in_tutor_export': 1, 'needs_api_decision': 1, 'needs_page_context': 1})
        self.assertEqual(result['uniquePendingDecisions'], 1)
        self.assertEqual(result['paidCalls'], 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((root / 'review/input-candidates.json').read_bytes(), candidates.read_bytes())


if __name__ == '__main__': unittest.main()
