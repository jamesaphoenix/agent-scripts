import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import PaidCache, request_key
from run import next_query
from source_health import held_profile_sources, positive_sites


class SourceHealthTests(unittest.TestCase):
    def test_only_positive_site_operators_restrict_a_search(self):
        self.assertEqual(positive_sites('-site:superprof.co.uk tutor Amersham'), set())
        self.assertEqual(positive_sites('(site:www.uktutors.com/tutor/ OR site:superprof.co.uk) "Amersham"'),
                         {'uktutors.com', 'superprof.co.uk'})

    def test_native_access_denial_is_distinguished_from_ordinary_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with sqlite3.connect(root / 'frontier.sqlite') as db:
                db.execute('CREATE TABLE pages (site TEXT, state TEXT, reason TEXT)')
                db.executemany('INSERT INTO pages VALUES (?,?,?)', [
                    ('superprof.co.uk', 'error', 'Go fetch: robots unavailable (status 403), held')] * 3 + [
                    ('uktutors.com', 'error', 'Go fetch: timeout')] * 5 + [
                    ('tutorful.co.uk', 'error', 'Go fetch: HTTP status 429')] * 2)
            value = held_profile_sources([root, root / 'missing'])
            self.assertEqual(set(value['sites']), {'superprof.co.uk'})
            self.assertEqual(value['sites']['superprof.co.uk']['deniedPages'], 3)
            self.assertEqual(value['errors'], [])
            with sqlite3.connect(root / 'frontier.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM pages').fetchone()[0], 10)

    def test_one_unreadable_source_does_not_discard_other_health_observations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'frontier.sqlite').write_text('not sqlite')
            value = held_profile_sources([root])
            self.assertEqual(value['sites'], {})
            self.assertEqual(len(value['errors']), 1)

    def test_blocked_queries_are_deferred_without_paid_reservations_and_resume_if_access_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            def query(keyword):
                payload = {'keyword': keyword}
                return {'lane': 'directories', 'key': request_key('directories', payload), 'payload': payload}
            blocked = query('site:superprof.co.uk tutor Amersham')
            healthy = query('site:uktutors.com tutor Amersham -site:superprof.co.uk')
            state = {'completedQueries': [], 'blockedProfileSites': {'superprof.co.uk': {}}}
            self.assertEqual(next_query('directories', [blocked, healthy], cache, state), healthy)
            self.assertNotIn(blocked['key'], state['completedQueries'])
            self.assertEqual(state['deferredQueries'][0]['key'], blocked['key'])
            self.assertEqual(cache.usage()['exposureCents'], 0)
            state['blockedProfileSites'] = {}
            self.assertEqual(next_query('directories', [blocked, healthy], cache, state), blocked)
            self.assertEqual(state['deferredQueries'], [])

    def test_grouped_queries_with_an_accessible_source_and_free_text_are_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            state = {'completedQueries': [], 'blockedProfileSites': {'superprof.co.uk': {}}}
            for keyword in ['(site:superprof.co.uk OR site:uktutors.com) tutor Amersham',
                            'tutor Amersham -site:superprof.co.uk']:
                item = {'lane': 'directories', 'key': keyword, 'payload': {'keyword': keyword}}
                self.assertEqual(next_query('directories', [item], cache, state), item)


if __name__ == '__main__':
    unittest.main()
