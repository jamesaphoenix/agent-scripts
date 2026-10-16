import hashlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'
sys.path.insert(0, str(SCRIPTS))
from directory_profiles import admitted_url, local_directory_link, profile_record
from lead_scope import matching_subjects
from qualification_context import profile_context_index
from universitytutor import bounded_frontier

URL = 'https://wendover.universitytutor.com/tutors/997974'
LISTING = 'https://wendover.universitytutor.com/wendover_tutoring'


def profile(subject='English', town='Wendover', canonical=URL):
    return f'''<head><link rel="canonical" href="{canonical}"></head>
      <nav>Maths and eleven plus tutors in Buckinghamshire</nav>
      <main><div><h3>Example K.</h3><p>Private tutor in {town}, United Kingdom</p></div>
      <section><h4>Education</h4><p>Music and engineering degree.</p></section>
      <section><h4>Experience</h4><p>I offer individual lessons.</p></section>
      <section><h4>Subject Expertise</h4><p>{subject}</p></section></main>
      <footer><h3>Popular Locations</h3>English maths 11+</footer>'''


class UniversityTutorTests(unittest.TestCase):
    def test_only_local_public_paths_and_bounded_pagination_are_admitted(self):
        for url in [URL, LISTING, LISTING + '/2', LISTING + '/30']:
            self.assertEqual(admitted_url(url), url)
        for url in [URL + '?email=show', URL + '/contact', LISTING + '/31',
                    'https://london.universitytutor.com/tutors/997974',
                    'https://wendover.universitytutor.com/admin',
                    'https://wendover.universitytutor.com:8443/tutors/997974']:
            self.assertIsNone(admitted_url(url))
        self.assertTrue(local_directory_link('https://aylesbury.universitytutor.com/tutors/864760', LISTING))
        self.assertTrue(local_directory_link(LISTING + '/2', LISTING))
        self.assertFalse(local_directory_link(LISTING + '/3', LISTING))
        self.assertFalse(local_directory_link('https://london.universitytutor.com/tutors/1', LISTING))

    def test_own_profile_fields_are_separate_from_navigation_and_qualifications(self):
        row = profile_record(profile(), URL)
        self.assertEqual(row['name'], 'Example K.')
        self.assertEqual(row['platform'], 'universitytutor.com')
        self.assertEqual(row['personId'], '997974')
        self.assertEqual(row['localityClaim'], 'Wendover')
        self.assertEqual(row['services'], ['English'])
        self.assertEqual(row['ownRoleQuote'], 'Private tutor in Wendover, United Kingdom')
        self.assertEqual(row['ownOfferExcerpt'], 'I offer individual lessons.')
        self.assertFalse(row['outreachEligible'])
        music = profile_record(profile(subject='Piano'), URL)
        self.assertEqual(music['services'], ['Piano'])
        self.assertEqual(matching_subjects(music, {'subjects': ['english', 'maths'],
            'includeElevenPlus': True, 'includePrimaryTutors': True, 'excludeMusicOnly': True}), [])
        outside = profile_record(profile(town='London'), URL)
        self.assertEqual(outside['localityClaim'], 'London')

    def test_conflicting_or_missing_own_fields_are_held(self):
        self.assertIsNone(profile_record(profile(canonical='https://wendover.universitytutor.com/tutors/123'), URL))
        self.assertIsNone(profile_record(profile().replace('<h4>Subject Expertise</h4>', '<h4>Recommendations</h4>'), URL))
        self.assertIsNone(profile_record(profile() + '<div><h3>Another Tutor</h3><p>Private tutor in Aylesbury, United Kingdom</p></div>', URL))
        extra = '<section><h4>Subject Expertise</h4><p>Maths</p></section>'
        self.assertIsNone(profile_record(profile().replace('</main>', extra + '</main>'), URL))
        self.assertEqual(profile_record(profile() + extra, URL)['services'], ['English'])

    def test_cli_dom_matches_imported_parser(self):
        cli = runpy.run_path(str(SCRIPTS / 'directory_profiles.py'), run_name='profile_cli_test')
        self.assertEqual(cli['profile_record'](profile(), URL)['services'], ['English'])

    def test_branch_has_fifteen_people_and_one_pagination_link(self):
        urls = [f'https://aylesbury.universitytutor.com/tutors/{i}' for i in range(1, 40)]
        links = bounded_frontier(urls + [LISTING + '/2', LISTING + '/3'])
        self.assertEqual(links, urls[:15] + [LISTING + '/2'])

    def test_qualification_replays_hash_checked_own_fields_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'receipts').mkdir(); (root / 'pages').mkdir()
            key = hashlib.sha256(URL.encode()).hexdigest(); body = profile().encode()
            page = root / 'pages' / (key + '.html'); page.write_bytes(body)
            (root / 'receipts' / (key + '.json')).write_text(json.dumps({
                'url': URL, 'status': 200, 'sha256': hashlib.sha256(body).hexdigest(), 'contentType': 'text/html'}))
            result = profile_context_index([root], [{'website': URL}])
            fields = json.loads(result[URL]['text'])['publicProfileFields']
            self.assertEqual(fields['services'], ['English'])
            self.assertEqual(fields['personId'], '997974')
            self.assertEqual(fields['ownRoleQuote'], 'Private tutor in Wendover, United Kingdom')
            self.assertEqual(fields['ownOfferExcerpt'], 'I offer individual lessons.')
            self.assertEqual(result[URL]['extractionKind'], 'public_profile_fields_v2')
            page.write_bytes(body + b'changed')
            self.assertEqual(profile_context_index([root], [{'website': URL}]), {})
