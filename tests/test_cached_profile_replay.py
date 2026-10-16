import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json
from directory_profiles import PageCache, profile_record, replay_cached_profiles

URL = 'https://www.tutorhunt.com/users/123456/'


def modern_profile(name='Jane', town='Amersham, Buckinghamshire', extra=''):
    return ('<h1>Search for tutors</h1><div id="profilenameinfo">'
            '<span id="profilenametxt">' + name + '</span><div id="profileloc">' + town + '</div></div>'
            '<table class="profilesubjectstattable"><tr><td class="marksub"></td>'
            '<td class="marksub">Maths</td></tr></table><div class="card">'
            '<h2>Information about ' + name + '</h2>I offer GCSE maths tuition.</div>' + extra)


class CachedProfileReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = {'seen': [URL], 'queue': ['https://www.tutorhunt.com/locations/amersham/'],
                      'records': [], 'attempts': 19, 'errors': []}
        self.body(URL)

    def body(self, url, text=None):
        cache = PageCache(self.root)
        page = cache.body_path(url)
        page.parent.mkdir(exist_ok=True)
        body = (text or modern_profile()).encode()
        page.write_bytes(body)
        atomic_json(self.root / 'receipts' / (cache.key(url) + '.json'),
                    {'url': url, 'status': 200, 'sha256': hashlib.sha256(body).hexdigest(),
                     'observedAt': '2026-10-13', 'contentType': 'text/html'})
        return page

    def replay(self, **kwargs):
        with mock.patch.object(PageCache, 'download', side_effect=AssertionError('Replay must not fetch')):
            return replay_cached_profiles(self.root, self.state, 2000, clock=lambda: 1000, **kwargs)

    def test_current_profile_uses_its_named_span_and_own_subjects(self):
        row = profile_record(modern_profile(), URL)
        self.assertEqual(row['name'], 'Jane')
        self.assertEqual(row['localityClaim'], 'Amersham, Buckinghamshire')
        self.assertEqual(row['services'], ['Maths'])
        self.assertEqual(row['personId'], '123456')
        self.assertFalse(row['outreachEligible'])

    def test_unavailable_and_neighbour_identity_are_not_promoted(self):
        row = profile_record(modern_profile(extra='<p>Not available to contact</p>'), URL)
        self.assertTrue(row['advertisedUnavailable'])
        bad = modern_profile().replace('Information about Jane', 'Information about Alex')
        self.assertIsNone(profile_record(bad, URL))
        repeated = modern_profile().replace('<span id="profilenametxt">Jane</span>',
            '<span id="profilenametxt">Jane</span><span id="profilenametxt">Alex</span>')
        self.assertIsNone(profile_record(repeated, URL))

    def test_replay_recovers_hashed_evidence_without_resetting_attempts_or_frontier(self):
        before = {k: json.dumps(self.state[k]) for k in ('seen', 'queue', 'attempts', 'errors')}
        result = self.replay()
        self.assertEqual(result['recoveredRecords'], 1)
        self.assertEqual(len(self.state['records']), 1)
        evidence = self.state['records'][0]['pageEvidence'][0]
        self.assertEqual(evidence['url'], URL)
        self.assertEqual(evidence['sha256'], hashlib.sha256(PageCache(self.root).body_path(URL).read_bytes()).hexdigest())
        self.assertEqual(before, {k: json.dumps(self.state[k]) for k in before})

    def test_replay_is_idempotent_and_recovers_only_unprocessed_new_cache_entries(self):
        self.replay()
        self.assertEqual(self.replay()['recoveredRecords'], 1)
        self.assertEqual(len(self.state['records']), 1)
        other = 'https://www.tutorhunt.com/users/654321/'
        self.state['seen'].append(other)
        self.body(other, modern_profile('Alex'))
        self.assertEqual(self.replay()['recoveredRecords'], 2)
        self.assertEqual(len(self.state['records']), 2)

    def test_corrupt_receipt_or_body_is_held_individually(self):
        other = 'https://www.tutorhunt.com/users/654321/'
        self.state['seen'].append(other)
        page = self.body(other, modern_profile('Alex'))
        page.write_text('changed body')
        result = self.replay()
        self.assertEqual(result['heldPages'], 1)
        self.assertEqual(result['recoveredRecords'], 1)
        self.assertEqual(self.state['records'][0]['name'], 'Jane')

    def test_unavailable_or_outside_advert_is_preserved_but_not_qualified(self):
        for text in [modern_profile(extra='<p>Not available to contact</p>'), modern_profile(town='Oxford')]:
            with self.subTest(text=text[-50:]):
                state = dict(self.state, records=[], cacheReplays={})
                self.body(URL, text)
                result = replay_cached_profiles(self.root, state, 2000, clock=lambda: 1000)
                self.assertEqual(result['recoveredRecords'], 1)
                self.assertEqual(json.loads((self.root / 'bucks-private-tutors.json').read_text()), [])

    def test_one_malformed_receipt_does_not_stop_the_other_cached_profiles(self):
        other = 'https://www.tutorhunt.com/users/654321/'
        self.state['seen'].insert(0, other)
        self.body(other, modern_profile('Alex'))
        (self.root / 'receipts' / (PageCache(self.root).key(other) + '.json')).write_text('invalid json')
        result = self.replay()
        self.assertEqual(result['heldPages'], 1)
        self.assertEqual(result['recoveredRecords'], 1)
        self.assertEqual(self.state['records'][0]['name'], 'Jane')

    def test_original_deadline_prevents_cache_work_and_resume_keeps_checkpoint(self):
        result = replay_cached_profiles(self.root, self.state, 999, clock=lambda: 1000)
        self.assertEqual(result['processedPages'], 0)
        self.assertEqual(self.state['records'], [])
        self.replay()
        saved = json.loads((self.root / 'state.json').read_text())
        resumed = replay_cached_profiles(self.root, saved, 2000, clock=lambda: 1000)
        self.assertEqual(resumed['processedPages'], 1)
        self.assertEqual(len(saved['records']), 1)


if __name__ == '__main__':
    unittest.main()
