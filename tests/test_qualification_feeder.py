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
from keyword_expansion import SearchIndex, keyword_plan
from qualification_feeder import refill, link_offers, tables, summary, local_directory_entry
from qualification_seeds import import_batches, publish
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

    def test_watcher_retries_just_expired_credential_cooldown_without_using_crash_recovery(self):
        atomic_json(self.root / 'job.json', self.job)
        atomic_json(self.root / 'status.json', {'phase': 'prepared'})
        with patch('qualify_inventory.subprocess_status', return_value=False), \
                patch('launch.credential_delay', side_effect=[5, 0]), \
                patch('qualify_inventory.time.sleep') as sleep, \
                patch('subprocess.run') as launch:
            watch(self.root, 'owner', 'monitor')
        sleep.assert_called_once_with(6)
        self.assertEqual(launch.call_args.args[0][1], 'kickstart')
        report = json.loads((self.root / 'monitor.json').read_text())
        self.assertEqual(report['action'], 'credential_retry_after_cooldown')
        self.assertEqual(report['recoveries'], 0)

    def test_watcher_does_not_wait_past_deadline_for_credential_cooldown(self):
        self.job['deadlineUnix'] = time.time() + 2
        atomic_json(self.root / 'job.json', self.job)
        atomic_json(self.root / 'status.json', {'phase': 'prepared'})
        with patch('qualify_inventory.subprocess_status', return_value=False), \
                patch('launch.credential_delay', return_value=5), \
                patch('qualify_inventory.time.sleep') as sleep, \
                patch('subprocess.run') as launch:
            watch(self.root, 'owner', 'monitor')
        sleep.assert_not_called()
        launch.assert_not_called()
        report = json.loads((self.root / 'monitor.json').read_text())
        self.assertEqual(report['action'], 'credential_cooldown')

    def watch_complete_window(self):
        self.job['expandOwnedContext'] = True
        atomic_json(self.root / 'job.json', self.job)
        atomic_json(self.root / 'status.json', {'phase': 'complete',
            'continuousSeeding': {'lastRefill': {'canRefill': False}}})
        with patch('qualify_inventory.subprocess_status', return_value=False), patch('subprocess.run') as launch:
            watch(self.root, 'owner', 'monitor')
        return launch, json.loads((self.root / 'monitor.json').read_text())

    def test_dry_continuous_window_waits_without_restarting_or_retiring_its_monitor(self):
        launch, report = self.watch_complete_window()
        launch.assert_not_called()
        self.assertFalse(report['terminal'])
        self.assertEqual(report['action'], 'await_new_sources')
        self.assertEqual(report['recoveries'], 0)

    def test_new_seed_restarts_a_drained_window_once_then_waits_again(self):
        publish(self.root, [{'name': 'Jane Smith', 'website': 'https://jane.example/'}],
                'https://local-directory.example/tutors')
        launch, report = self.watch_complete_window()
        self.assertEqual(launch.call_args.args[0][1], 'kickstart')
        self.assertEqual(report['pendingWebsiteSeedBatches'], 1)
        self.assertEqual(report['action'], 'refresh_saved_context')
        import_batches(self.root, self.db)
        launch, report = self.watch_complete_window()
        launch.assert_not_called()
        self.assertEqual(report['pendingWebsiteSeedBatches'], 0)

    def test_held_seed_is_not_replayed_by_the_monitor(self):
        publish(self.root, [{'name': 'Private', 'website': 'https://bad.example/login'}],
                'https://local-directory.example/tutors')
        import_batches(self.root, self.db)
        launch, report = self.watch_complete_window()
        launch.assert_not_called()
        self.assertEqual(report['pendingWebsiteSeedBatches'], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM website_seed_batches WHERE state='held'").fetchone()[0], 1)

    def test_new_seeds_never_extend_an_expired_window(self):
        publish(self.root, [{'name': 'Jane Smith', 'website': 'https://jane.example/'}],
                'https://local-directory.example/tutors')
        self.job['deadlineUnix'] = time.time() - 1
        launch, report = self.watch_complete_window()
        self.assertEqual(launch.call_args.args[0][1], 'bootout')
        self.assertTrue(report['terminal'])

    def test_new_seeds_cannot_resume_a_budget_or_api_stop(self):
        publish(self.root, [{'name': 'Jane Smith', 'website': 'https://jane.example/'}],
                'https://local-directory.example/tutors')
        self.job['expandOwnedContext'] = True
        atomic_json(self.root / 'job.json', self.job)
        for phase in ('budget_reached', 'api_unavailable'):
            with self.subTest(phase=phase):
                atomic_json(self.root / 'status.json', {'phase': phase})
                with patch('qualify_inventory.subprocess_status', return_value=False), patch('subprocess.run') as launch:
                    watch(self.root, 'owner', 'monitor')
                self.assertEqual(launch.call_args.args[0][1], 'bootout')

    def test_foreign_place_collision_is_not_an_exploration_seed(self):
        _, page = self.candidate('https://local-directory.example/tutors', links=[
            {'url': 'https://learnmate.com.au/tutors/winslow', 'anchor': 'Maths tutor in Winslow'}])
        self.assertEqual(refill(self.root, self.job, self.db, SCOPE, {page['url']: page})['lastRefill']['added'], 0)

    def test_directory_indexes_go_to_registered_worker_without_model_classification(self):
        root = self.root / 'profiles'; root.mkdir(); self.job['profileRoots'] = [str(root)]
        _, page = self.candidate('https://local-directory.example/tutors', links=[
            {'url': 'https://www.tutorhunt.com/subjects/eleven-plus/locations/amersham/', 'anchor': '11 plus tutors in Amersham'},
            {'url': 'https://www.tutorhunt.com/subjects/biology/locations/amersham/', 'anchor': 'Biology tutors in Amersham'}])
        result = refill(self.root, self.job, self.db, SCOPE, {page['url']: page})['lastRefill']
        self.assertEqual(result['added'], 0)
        self.assertEqual(len(result['delegatedDirectoryEntrypoints']), 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM candidates').fetchone()[0], 1)
        self.assertEqual(len(list((root / 'seed-batches').glob('*.json'))), 1)

    def test_delegated_profile_waits_do_not_starve_the_website_crawler(self):
        self.search()
        with self.db:
            for number in range(50):
                self.db.execute('INSERT INTO candidates(key,candidate,state) VALUES(?,?,?)',
                    (f'waiting-{number}', json.dumps({'name':'Waiting tutor','website':f'https://www.childcare.co.uk/profile/{number+1}'}), 'awaiting_profile_context'))
        result = refill(self.root, self.job, self.db, SCOPE, {})
        self.assertEqual(result['lastRefill']['added'], 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM candidates WHERE state='awaiting_profile_context'").fetchone()[0], 50)

    def test_directory_owner_is_delegated_once_across_parents_and_receipt_changes(self):
        root = self.root / 'profiles'; root.mkdir(); self.job['profileRoots'] = [str(root)]
        url = 'https://www.tutorhunt.com/subjects/maths/locations/amersham/'
        _, first = self.candidate('https://first-directory.example/tutors', links=[
            {'url': url, 'anchor': 'Maths tutors in Amersham'}])
        self.assertEqual(len(refill(self.root, self.job, self.db, SCOPE, {first['url']: first})['lastRefill']['delegatedDirectoryEntrypoints']), 1)
        _, second = self.candidate('https://second-directory.example/tutors', links=[
            {'url': url, 'anchor': 'Maths tutors in Amersham'}])
        result = refill(self.root, self.job, self.db, SCOPE, {second['url']: second})['lastRefill']
        self.assertEqual(result['delegatedDirectoryEntrypoints'], [])
        self.assertFalse(result['canRefill'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM qualification_feed WHERE state='delegated_source'").fetchone()[0], 1)

    def test_dry_queue_records_search_demand_without_starting_paid_search_or_respinning(self):
        towns = [{'name': 'Amersham', 'coordinate': '51.674,-0.607'}]
        self.index.configure(towns)
        self.index.add_queries(keyword_plan(towns, ['11+ tutor']))
        result = refill(self.root, self.job, self.db, SCOPE, {})['lastRefill']
        self.assertEqual(result['websiteQueue'], 0)
        self.assertEqual(result['searchReplenishment']['pendingQueries'], 2)
        self.assertEqual(result['paidSearchCalls'], 0)
        self.assertEqual(result['nextAction'], 'await_profiles_or_search_authorization')
        self.assertFalse(result['canRefill'])

    def test_directory_route_rejects_nearby_and_foreign_place_collisions(self):
        for url in ('https://www.tutorhunt.com/subjects/maths/locations/burnham-on-sea/',
                    'https://www.tutorhunt.com/subjects/eleven-plus/locations/towcester/',
                    'https://www.tutorhunt.com/subjects/maths/locations/cookham-dean/',
                    'https://www.findtutors.co.uk/english-tutors-wendover-utah/',
                    'https://www.uktutors.com/tutor-regions/private-maths-tutors-and-tuition-in-haddenham-cambridgeshire/'):
            self.assertFalse(local_directory_entry(url), url)
        for url in ('https://www.tutorhunt.com/subjects/maths/locations/farnham-common/',
                    'https://www.tutorhunt.com/subjects/maths/locations/burnham-buckinghamshire/',
                    'https://www.findtutors.co.uk/english-tutors-high-wycombe/?page=2',
                    'https://www.childcare.co.uk/search/Private-Tutors/High+Wycombe/10-miles/Maths-Tutor',
                    'https://www.uktutors.com/tutor-regions/private-maths-tutors-and-tuition-in-marlow/'):
            self.assertTrue(local_directory_entry(url), url)


if __name__ == '__main__': unittest.main()
