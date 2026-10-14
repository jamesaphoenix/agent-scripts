from pathlib import Path
import hashlib
import json
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import PaidCache, atomic_json, request_key
from directory_profiles import profile_record
from keyword_expansion import SearchIndex
from qualification_feeder import refill, link_offers, tables, summary
from qualification_seeds import import_batches
from qualify_inventory import create_database, candidate_key, watch
from test_tta_scraper import profile as tta_profile

SCOPE = {'subjects': ['english', 'maths'], 'includeElevenPlus': True,
         'includePrimaryTutors': True, 'excludeMusicOnly': True}


class QualificationFeederTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = create_database(self.root / 'candidates.sqlite'); self.addCleanup(self.db.close)
        import_batches(self.root, self.db)
        tables(self.db)
        self.index = SearchIndex(self.root / 'keywords.sqlite'); self.addCleanup(self.index.close)
        self.job = {'deadlineUnix': time.time() + 60, 'towns': ['Amersham'],
                    'keywordIndexPath': str(self.index.path)}

    def search(self, count=3):
        root = self.root / 'search'; cache = PaidCache(root, 200, 0)
        payload = {'keyword': 'maths tutor Amersham', 'language_code': 'en',
                   'location_coordinate': '51.674,-0.607,200', 'depth': 30}
        key = request_key('organic', payload)
        cache.reserve(key, 'organic', payload, 100)
        cache.settle(key, {'status_code': 20000, 'cost': .002, 'tasks': [{'id': 'task-one', 'cost': .002,
            'status_code': 20000, 'result': [{'items': [{'url': f'https://tutor-{i}.example/',
                'title': f'Jane Tutor {i} in Amersham'} for i in range(count)]}]}]})
        cache.db.close()
        self.index.sync_run(root, [{'name': 'Amersham', 'coordinate': '51.674,-0.607'}])
        return root / 'receipts' / (key + '.json')

    def candidate(self, url, state='source_to_expand', text='Maths tutors in Amersham.', links=()):
        candidate = {'name': 'Local tutors', 'website': url, 'sourceUrl': url}
        key = candidate_key(candidate)
        page = {'url': url, 'text': text, 'links': list(links),
                'replaySource': {'checkpoint': '/saved/crawl.json', 'sha256': 'source-sha'}}
        with self.db:
            self.db.execute('INSERT INTO candidates(key,candidate,state,evidence) VALUES(?,?,?,?)',
                (key, json.dumps(candidate), state, json.dumps([page])))
        return key, page

    def test_verified_search_intake_is_read_only_and_restart_preserves_holds(self):
        self.search()
        before = self.index.db.total_changes
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 3)
        self.assertEqual(result['lastRefill']['paidSearchCalls'], 0)
        self.assertEqual(self.index.db.total_changes, before)
        with self.db: self.db.execute("UPDATE candidates SET state='fetch_held',error='403'")
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM candidates WHERE state='fetch_held'").fetchone()[0], 3)
        rows = [json.loads(r[0]) for r in self.db.execute('SELECT candidate FROM candidates')]
        self.assertTrue(all(r['outreachEligible'] is False for r in rows))

    def test_changed_receipt_and_forged_url_are_held(self):
        receipt = self.search()
        receipt.write_text(receipt.read_text() + ' ')
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['held'], 3)
        self.assertEqual(self.db.execute('SELECT count(*) FROM candidates').fetchone()[0], 0)

    def test_receipt_must_contain_the_url_not_just_have_a_valid_hash(self):
        self.search(1)
        with self.index.db:
            self.index.db.execute("UPDATE upstream_urls SET url='https://forged-tutor.example/'")
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['held'], 1)

    def test_deadline_low_water_and_branch_limits_survive_refills(self):
        self.search(35)
        self.job['deadlineUnix'] = time.time() - 1
        refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(self.db.execute('SELECT count(*) FROM candidates').fetchone()[0], 0)
        self.job['deadlineUnix'] = time.time() + 60
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 15)
        with self.db: self.db.execute("UPDATE candidates SET state='excluded_irrelevant'")
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 0)
        with patch('qualification_feeder.LOW_WATER', 1):
            self.db.execute("UPDATE candidates SET state='pending'"); self.db.commit()
            runs = self.db.execute('SELECT count(*) FROM qualification_feed_runs').fetchone()[0]
            refill(self.root, self.job, self.db, SCOPE, {})
            self.assertEqual(self.db.execute('SELECT count(*) FROM qualification_feed_runs').fetchone()[0], runs)

    def test_local_directories_bridge_to_observed_tutor_sites_without_borrowing_proof(self):
        key, page = self.candidate('https://local-directory.example/tutors', links=[
            {'url': 'https://new-tutor.example/', 'anchor': 'Independent maths tutor'},
            {'url': 'https://other.example/privacy', 'anchor': 'Tutor privacy'},
            {'url': 'https://shop.example/', 'anchor': 'Our shop'}])
        result = refill(self.root, self.job, self.db, SCOPE, {page['url']: page})
        self.assertEqual(result['lastRefill']['added'], 1)
        child = json.loads(self.db.execute("SELECT candidate FROM candidates WHERE key<>?", (key,)).fetchone()[0])
        self.assertEqual(child['website'], 'https://new-tutor.example/')
        self.assertNotIn('advertisedAddress', child)
        self.assertEqual(child['provenance'][0]['parent'], key)

    def test_weak_outside_and_changed_directory_context_cannot_expand(self):
        _, page = self.candidate('https://directory.example/tutors', text='Music tutors in London.', links=[
            {'url': 'https://new-tutor.example/', 'anchor': 'Maths tutor'}])
        self.assertEqual(link_offers(self.db, {page['url']: page}, SCOPE, ['Amersham'], set()), [])
        page['text'] = 'Maths tutors in Amersham.'
        self.assertEqual(link_offers(self.db, {page['url']: page}, SCOPE, ['Amersham'], set()), [])

    def test_lineage_does_not_become_a_fresh_root_and_stops_after_two_hops(self):
        key, page = self.candidate('https://local-tutor.example/', state='tutor_research_candidate', links=[
            {'url': 'https://next-tutor.example/', 'anchor': 'Maths tutor'}])
        tables(self.db)
        with self.db:
            self.db.execute('INSERT INTO qualification_feed VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('ancestor', page['url'], 'links:ancestor', 'parent', 'original-root', 2, 'admitted', key, '{}', None, 'now'))
        self.assertEqual(link_offers(self.db, {page['url']: page}, SCOPE, ['Amersham'], set()), [])

    def test_source_yield_uses_qualification_outcomes_and_combined_owner_cap(self):
        self.search()
        with patch('qualification_feeder.MAX_OWNERS', 2):
            result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 2)
        with self.db:
            self.db.execute("UPDATE candidates SET state='tutor_research_candidate',decision='{}'")
        self.assertEqual(summary(self.db)['sourceYield'][0]['subjectLocalMatches'], 2)
        self.assertEqual(summary(self.db)['sourceYield'][0]['classified'], 2)

    def test_profile_website_requires_its_own_cached_identity_subjects_and_locality(self):
        root = self.root / 'profiles'; root.mkdir()
        source = 'https://thetutorsassociation.wildapricot.org/Sys/PublicProfile/99590192/6119301'
        html = tta_profile(**{'Business Website': 'https://jane-tutor.example/'}).encode()
        key = hashlib.sha256(source.encode()).hexdigest()
        (root / 'pages').mkdir(); (root / 'pages' / (key + '.html')).write_bytes(html)
        atomic_json(root / 'receipts' / (key + '.json'), {'url': source, 'status': 200,
            'sha256': hashlib.sha256(html).hexdigest()})
        atomic_json(root / 'bucks-private-tutors.json', [profile_record(html.decode(), source)])
        self.job['profileRoots'] = [str(root)]
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 1)
        self.assertEqual(result['sourceYield'][0]['source'], 'profile:thetutorsassociation.wildapricot.org')
        # A different advertised site in the export cannot borrow this body.
        hint = profile_record(html.decode(), source); hint['website'] = 'https://forged-tutor.example/'
        atomic_json(root / 'bucks-private-tutors.json', [hint])
        with self.db: self.db.execute("UPDATE candidates SET state='fetch_held'")
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 0)

    def test_broken_source_does_not_stop_other_sources(self):
        _, page = self.candidate('https://local-directory.example/tutors', links=[
            {'url': 'https://new-tutor.example/', 'anchor': 'Independent maths tutor'}])
        with patch('qualification_feeder.search_offers', side_effect=ValueError('Damaged index')):
            result = refill(self.root, self.job, self.db, SCOPE, {page['url']: page})
        self.assertEqual(result['lastRefill']['added'], 1)
        self.assertEqual(result['lastRefill']['sourceErrors'][0]['source'], 'search')

    def test_watcher_refills_after_profile_producer_finishes_without_extending_deadline(self):
        atomic_json(self.root / 'job.json', self.job)
        atomic_json(self.root / 'status.json', {'phase': 'complete',
            'continuousSeeding': {'lastRefill': {'canRefill': True}}})
        with patch('qualify_inventory.subprocess_status', return_value=False), patch('subprocess.run') as launch:
            watch(self.root, 'owner', 'monitor')
        self.assertEqual(launch.call_args.args[0][1], 'kickstart')
        self.job['deadlineUnix'] = time.time() - 1
        atomic_json(self.root / 'job.json', self.job)
        with patch('qualify_inventory.subprocess_status', return_value=False), patch('subprocess.run') as launch:
            watch(self.root, 'owner', 'monitor')
        self.assertEqual(launch.call_args.args[0][1], 'bootout')


if __name__ == '__main__': unittest.main()
