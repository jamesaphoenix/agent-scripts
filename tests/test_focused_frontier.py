import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from focused_frontier import Frontier, Policy, link_signals


def decision(kind='independent_tutor', niche=.98, local=.95, outside=.01):
    return {'kind': kind, 'kindProbability': .96, 'niche': niche, 'local': local,
            'outside': outside, 'eleven_plus': .8, 'decisionKey': 'a' * 64}


class FrontierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy = Policy(['Amersham', 'High Wycombe'])
        self.queue = Frontier(Path(self.temp.name) / 'frontier.sqlite', self.policy)
        self.addCleanup(self.queue.close)

    def seed(self, url='https://solo.example.org/', group='maps-amersham'):
        self.queue.add(url, {'title': 'Private tutor in Amersham'}, group=group)
        return self.queue.claim()

    def test_larger_directory_allowance_does_not_expand_ordinary_sites_or_recursion(self):
        policy = Policy(['Amersham'], max_pages=10000, directory_per_site=4000)
        policy.validate()
        self.assertEqual((policy.per_site, policy.max_depth, policy.branch_sites), (8, 2, 15))
        for policy in [Policy(['Amersham'], directory_per_site=4001),
                       Policy(['Amersham'], max_pages=10001),
                       Policy(['Amersham'], per_site=9)]:
            with self.assertRaises(ValueError):
                policy.validate()

    def complete(self, row, result=None, links=()):
        return self.queue.complete(row['key'], {'url': row['url'], 'text': 'I tutor maths in Amersham.',
                                   'links': list(links)}, result or decision())

    def test_irrelevant_and_outside_pages_never_expand(self):
        for result in [decision('irrelevant', .1), decision(outside=.95, local=.01), decision('institution')]:
            with self.subTest(result=result):
                row = self.seed('https://site' + str(self.queue.summary()['pages']) + '.example.org/')
                self.complete(row, result, [{'url': 'https://newtutor.co.uk/', 'anchor': 'Private tutor'}])
                self.assertEqual(self.queue.summary()['states'].get('pending', 0), 0)

    def test_only_relevant_links_expand_and_external_branch_never_exceeds_fifteen(self):
        row = self.seed()
        links = [{'url': f'https://tutor{i}.co.uk/', 'anchor': 'Private tutor'} for i in range(30)]
        links += [{'url': 'https://hostingvendor.co.uk/', 'anchor': 'Web design'},
                  {'url': row['url'] + 'contact', 'anchor': 'Contact'}]
        self.complete(row, links=links)
        self.assertEqual(self.queue.summary()['states']['pending'], 16)
        self.assertFalse(self.queue.db.execute("SELECT 1 FROM pages WHERE url LIKE '%hostingvendor%'").fetchone())

    def test_uncertain_has_only_one_contact_hop_and_cannot_repeat_drift(self):
        row = self.seed()
        self.complete(row, decision('uncertain', niche=.6, local=.3), [
            {'url': row['url'] + 'about', 'anchor': 'About'},
            {'url': row['url'] + 'contact', 'anchor': 'Contact'},
            {'url': 'https://newtutor.co.uk/', 'anchor': 'Tutor'}])
        child = self.queue.claim()
        self.assertEqual(child['drift'], 1)
        self.complete(child, decision('uncertain', niche=.6), [{'url': row['url'] + 'team', 'anchor': 'About team'}])
        self.complete(self.queue.claim(), decision('uncertain', niche=.6))
        self.assertIsNone(self.queue.claim())

    def test_lineage_depth_is_not_reset_by_seed_reimport(self):
        row = self.seed()
        for i in range(2):
            self.complete(row, links=[{'url': f'https://child{i}.co.uk/', 'anchor': 'Tutor'}])
            row = self.queue.claim()
        self.queue.add(row['url'], {'title': 'Tutor'}, group='fresh-root')
        self.assertEqual(self.queue.db.execute('SELECT depth FROM pages WHERE key=?', (row['key'],)).fetchone()[0], 2)
        self.complete(row, links=[{'url': 'https://too-far.co.uk/', 'anchor': 'Tutor'}])
        self.assertIsNone(self.queue.claim())

    def test_productive_sources_outrank_unproductive_sources_with_same_relevance(self):
        good = self.seed(group='good')
        self.complete(good)
        bad = self.seed('https://notatutor.co.uk/', group='bad')
        self.complete(bad, decision('irrelevant', niche=.01))
        self.queue.add('https://newgood.co.uk/', {'title': 'Tutor'}, group='good')
        self.queue.add('https://newbad.co.uk/', {'title': 'Tutor'}, group='bad')
        self.assertEqual(self.queue.claim()['group_key'], 'good')

    def test_same_domain_does_not_inflate_yield(self):
        for path in ['/', '/about']:
            row = self.seed('https://solo.co.uk' + path)
            self.complete(row)
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM source_yield').fetchone()[0], 1)

    def test_six_distinct_failed_domains_stop_recursive_branch(self):
        root = self.seed()
        self.complete(root, decision('directory'), [{'url': f'https://child{i}.co.uk/', 'anchor': 'Tutor'} for i in range(8)])
        for _ in range(6): self.complete(self.queue.claim(), decision('irrelevant', niche=.01))
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.queue.summary()['states']['pruned'], 2)

    def test_tutor_root_cannot_mask_unproductive_child_branch(self):
        root = self.seed()
        self.complete(root, decision(), [{'url': f'https://child{i}.co.uk/', 'anchor': 'Tutor'} for i in range(8)])
        for _ in range(6): self.complete(self.queue.claim(), decision('irrelevant', niche=.01))
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.queue.summary()['states']['pruned'], 2)

    def test_cap_and_claim_survive_restart_without_duplicate_content_attempts(self):
        row = self.seed()
        reopened = Frontier(self.queue.path, self.policy)
        try:
            reopened.recover()
            claimed = reopened.claim()
            self.assertEqual(claimed['key'], row['key'])
            self.assertEqual(claimed['attempts'], 1)
        finally: reopened.close()

    def test_binary_private_social_and_tracker_links_are_not_scheduled(self):
        for url in ['http://127.0.0.1/', 'https://facebook.com/tutor', 'https://a.co.uk/paper.pdf', 'https://google.com/search?q=tutor']:
            self.assertIsNone(self.queue.add(url, {'title': 'Tutor'}))

    def test_known_directory_website_links_can_find_personal_domains(self):
        row = self.seed('https://tutorperch.com/t/jane-smith')
        self.complete(row, decision('directory'), [
            {'url': 'https://janesmith.co.uk/', 'anchor': 'Website'},
            {'url': 'https://designer.co.uk/', 'anchor': 'Website by a designer'}])
        child = self.queue.claim()
        self.assertEqual(child['url'], 'https://janesmith.co.uk/')
        self.complete(child, decision('irrelevant', niche=.01), [{'url': 'https://designblog.co.uk/', 'anchor': 'Tutor'}])
        self.assertIsNone(self.queue.claim())

    def test_directory_profile_allowance_is_bounded_without_loosening_ordinary_sites(self):
        for i in range(210):
            self.queue.add('https://tutorperch.com/t/person-' + str(i), {'title': 'Tutor in Amersham'})
            self.queue.add('https://solo.co.uk/page/' + str(i), {'title': 'Tutor in Amersham'})
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM pages WHERE site='tutorperch.com'").fetchone()[0], 200)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM pages WHERE site='solo.co.uk'").fetchone()[0], 8)

    def test_named_tutorhunt_profiles_expand_without_tutor_words_in_the_link(self):
        row = self.seed('https://www.tutorhunt.com/locations/amersham/')
        self.complete(row, decision('directory'), [
            {'url': 'https://www.tutorhunt.com/users/jane/', 'anchor': 'Jane'},
            {'url': 'https://www.tutorhunt.com/login.asp', 'anchor': 'Login'}])
        child = self.queue.claim()
        self.assertEqual(child['url'], 'https://www.tutorhunt.com/users/jane/')
        self.complete(child, decision('directory'))
        self.assertEqual(self.queue.summary()['localProfileCandidates'], 1)
        self.assertEqual(self.queue.summary()['localTutorDomains'], 0)

    def test_profile_candidates_on_one_directory_are_counted_separately(self):
        for name in ('jane-smith', 'joe-smith'):
            row = self.seed('https://tutorperch.com/t/' + name)
            self.complete(row, decision('directory'))
        self.assertEqual(self.queue.summary()['localProfileCandidates'], 2)
        self.assertEqual(self.queue.summary()['localTutorDomains'], 0)

    def test_three_access_denials_hold_host_without_retrying_other_paths(self):
        for i in range(6):
            self.queue.add(f'https://blocked.co.uk/tutor/{i}', {'title': 'Tutor'})
        for _ in range(3):
            self.queue.hold(self.queue.claim()['key'], 'Go fetch: page status 403', state='error')
        self.queue.add('https://good.co.uk/', {'title': 'Tutor'})
        self.assertEqual(self.queue.claim()['site'], 'good.co.uk')
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.queue.summary()['states']['blocked'], 3)

    def test_404_does_not_block_whole_host(self):
        for i in range(5):
            self.queue.add(f'https://active.co.uk/tutor/{i}', {'title': 'Tutor'})
        for _ in range(3):
            self.queue.hold(self.queue.claim()['key'], 'Go fetch: page status 404', state='error')
        self.assertIsNotNone(self.queue.claim())

    def test_queue_selection_queries_do_not_grow_with_pending_page_count(self):
        for i in range(400):
            self.queue.add(f'https://tutor{i}.co.uk/', {'title': 'Tutor in Amersham'})
        queries = []
        self.queue.db.set_trace_callback(queries.append)
        try:
            self.assertIsNotNone(self.queue.claim())
        finally:
            self.queue.db.set_trace_callback(None)
        self.assertLess(len(queries), 25)

    def test_concurrent_claims_exclude_active_host(self):
        self.queue.add('https://solo.co.uk/about', {'title': 'Tutor'})
        self.queue.add('https://solo.co.uk/contact', {'title': 'Tutor'})
        self.queue.add('https://other.co.uk/', {'title': 'Tutor'})
        first = self.queue.claim()
        second = self.queue.claim(exclude_sites={first['site']})
        self.assertNotEqual(first['site'], second['site'])

    def test_missing_link_url_does_not_stop_expansion(self):
        row = self.seed()
        self.complete(row, links=[{'anchor': 'Tutor'}, {'url': row['url']+'about', 'anchor': 'About'}])
        self.assertIsNotNone(self.queue.claim())

    def test_audited_profile_urls_are_skipped_without_losing_existing_evidence(self):
        url = 'https://tutorperch.com/t/jane-smith'
        self.queue.add(url, {'title': 'Tutor'})
        self.queue.known_profiles = frozenset({'tutorperch.com/t/jane-smith'})
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.queue.summary()['states'], {'known': 1})
        self.queue.known_profiles = frozenset({'tutorperch.com/t/jane-smith', 'tutorperch.com/t/joe-smith'})
        self.assertIsNone(self.queue.add('https://tutorperch.com/t/joe-smith', {'title': 'Tutor'}))

    def test_county_service_claim_does_not_require_one_particular_town(self):
        row = self.seed()
        self.queue.complete(row['key'], {'text': 'I offer private maths tuition across Buckinghamshire.', 'links': []}, decision())
        self.assertEqual(self.queue.summary()['localTutorDomains'], 1)

    def test_url_locality_without_page_text_is_not_accepted(self):
        row = self.seed('https://solo.co.uk/buckinghamshire/')
        self.queue.complete(row['key'], {'text': 'I offer private maths tuition.', 'links': []}, decision())
        self.assertEqual(self.queue.summary()['localTutorDomains'], 0)


if __name__ == '__main__': unittest.main()
