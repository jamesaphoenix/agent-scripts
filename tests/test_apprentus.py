import hashlib
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from directory_profiles import admitted_url, profile_record, listing_records, listing_profile_urls, local_directory_link, exclusion_reason, replay_cached_profiles
from audit_directory_identities import audit_rows
from discovery import atomic_json, professional_profile, website_key, hold_reason

HOST = 'https://www.apprentus.co.uk'
URL = HOST + '/private-lessons/high-wycombe/tutoring/english/primary-maths-english'


def advert(name='Rebecca Jade', handle='rebecca.jade', place='Around High Wycombe, United Kingdom', subject='English'):
    return f'''<nav>Maths and English tutors in Buckinghamshire</nav><div id="left-section">
        <a class="name mobile-name" href="/in/{handle}">{name}</a>
        <div class="course-title">Primary English tuition</div><div class="skill-title">Skills</div><a class="skill-title">{subject}</a>
        <div class="course-description">I teach primary pupils.</div>
        <div class="location-container"><div class="locations-list"><div class="location">{place}</div></div></div>
        </div><aside><a class="name" href="/in/neighbour">Anam Ali</a><div class="course-description">I live in London.</div></aside>'''


class ApprentusTests(unittest.TestCase):
    def test_each_branch_keeps_fifteen_unique_sources_with_adverts_first(self):
        from apprentus import bounded_frontier
        profiles = [URL + str(i) for i in range(20)]
        self.assertEqual(bounded_frontier(profiles[:4], [profiles[0], *profiles[4:]]), profiles[:15])

    def test_only_public_adverts_and_bounded_local_listing_paths_are_admitted(self):
        self.assertEqual(admitted_url(URL + '?from_landing_page=true'), URL)
        self.assertEqual(admitted_url(HOST + '/tutoring-lessons/high-wycombe'), HOST + '/tutoring-lessons/high-wycombe')
        for path in ['/in/rebecca.jade', '/profile', '/profiles/42', '/popups/message/42', '/login', '/_kea.json/search', '/math-lessons/london',
                     '/private-lessons/london/tutoring/english/primary-english']:
            self.assertIsNone(admitted_url(HOST + path), path)

    def test_advert_uses_owned_teacher_handle_subjects_and_lesson_location(self):
        row = profile_record(advert(), URL)
        self.assertIsNotNone(row)
        self.assertEqual((row['name'], row['personId'], row['localityClaim'], row['services']),
                         ('Rebecca Jade', 'rebecca.jade', 'High Wycombe', ['English']))
        self.assertIsNone(exclusion_reason(row))
        self.assertFalse(row['outreachEligible'])
        self.assertEqual(profile_record(advert(place='London, United Kingdom'), URL)['localityClaim'], 'London')
        self.assertTrue(profile_record(advert(place='High Wycombe, United States'), URL)['locationConflict'])
        self.assertIsNone(profile_record(advert().replace('id="left-section"', 'id="related"'), URL))
        self.assertIsNone(profile_record(advert().replace('class="skill-title"', 'class="related-subject"'), URL))

    def test_multiple_owned_locations_or_headers_are_not_silently_combined(self):
        text = advert().replace('</div></div></div>', '</div><div class="location">London, United Kingdom</div></div></div>')
        self.assertTrue(profile_record(text, URL)['locationConflict'])
        self.assertIsNone(profile_record(advert() + advert(), URL))

    def test_owned_address_uses_town_after_street_and_retains_only_localities(self):
        row = profile_record(advert(place='Shrubbery Road, High Wycombe, UK'), URL)
        self.assertEqual(row['localityClaim'], 'High Wycombe')
        self.assertEqual(row['ownLessonLocationClaims'], ['High Wycombe'])
        text = advert(place='High Wycombe HP10, UK').replace('</div></div></div>',
                    '</div><div class="location">marlow</div></div></div>')
        row = profile_record(text, URL)
        self.assertEqual(row['localityClaim'], 'High Wycombe')
        self.assertFalse(row['locationConflict'])
        self.assertIsNone(exclusion_reason(row))

    def test_generic_tutoring_tag_can_use_the_owned_explicit_subject_list(self):
        text = advert(subject='Tutoring').replace('Primary English tuition',
                        'GCSE or below Math, English, Physics, Chemistry, Computer Science')
        self.assertEqual(profile_record(text, URL)['services'],
                         ['Tutoring', 'Math', 'English', 'Physics', 'Chemistry', 'Computer Science'])
        text = advert(subject='Tutoring').replace('Primary English tuition', 'French tutor with a maths degree')
        self.assertEqual(profile_record(text, URL)['services'], ['Tutoring'])

    def test_cache_replay_updates_existing_advert_without_resetting_or_refetching(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); body = advert(place='Shrubbery Road, High Wycombe, UK').encode()
            key = hashlib.sha256(URL.encode()).hexdigest(); sha = hashlib.sha256(body).hexdigest()
            page = root / 'pages' / (key + '.html'); page.parent.mkdir(); page.write_bytes(body)
            atomic_json(root / 'receipts' / (key + '.json'), {'url': URL, 'sha256': sha, 'status': 200, 'observedAt': '2026-10-14T03:00:00+00:00'})
            old = dict(profile_record(body.decode(), URL), localityClaim='Shrubbery Road')
            state = {'seen': [URL], 'queue': [], 'records': [old], 'attempts': 9, 'errors': []}
            with patch('directory_profiles.PageCache.download', side_effect=AssertionError('Unexpected HTTP')):
                report = replay_cached_profiles(root, state, time.time() + 60, platform='apprentus')
                self.assertEqual(report['updatedRecords'], 1)
                replay_cached_profiles(root, state, time.time() + 60, platform='apprentus')
            self.assertEqual((state['attempts'], state['seen'], len(state['records'])), (9, [URL], 1))
            self.assertEqual(state['records'][0]['localityClaim'], 'High Wycombe')

    def test_local_listing_seeds_exclude_worldwide_recommendations(self):
        def card(source, name='Rebecca Jade'):
            return f'<div class="search-result"><a class="result-content" href="{source}"><h1 class="name">{name}</h1><h3 class="title title-desktop">English tuition</h3></a></div>'
        outside = HOST + '/private-lessons/paris/tutoring/english/primary-english'
        text = '<div id="main-search-results">' + card(URL) + card(outside, 'Another Tutor') + '</div>'
        rows = listing_records(text, HOST + '/tutoring-lessons/high-wycombe')
        self.assertEqual(listing_profile_urls(rows, 'www.apprentus.co.uk'), [URL])
        self.assertEqual(rows[0]['evidenceKind'], 'directory_listing')
        self.assertFalse(local_directory_link(outside, HOST + '/tutoring-lessons/high-wycombe'))
        self.assertTrue(local_directory_link(HOST + '/math-lessons/amersham', HOST + '/tutoring-lessons/high-wycombe'))
        self.assertFalse(local_directory_link(HOST + '/math-lessons/london', HOST + '/tutoring-lessons/high-wycombe'))

    def test_different_teachers_do_not_share_a_platform_business_identity(self):
        other = HOST + '/private-lessons/aylesbury/tutoring/math/primary-maths'
        self.assertTrue(professional_profile(URL))
        self.assertNotEqual(website_key(URL), website_key(other))
        self.assertEqual(hold_reason({'name': 'Apprentus', 'website': HOST + '/math-lessons/amersham'}),
                         'source_directory_or_job_page')

    def test_frozen_body_audit_proves_full_owned_name_and_holds_initials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, expected in [('Rebecca Jade', 'rebecca jade'), ('Rebecca J', None)]:
                body = advert(name=name).encode(); key = hashlib.sha256(URL.encode()).hexdigest(); sha = hashlib.sha256(body).hexdigest()
                page = root / 'pages' / (key + '.html');page.parent.mkdir(exist_ok=True);page.write_bytes(body)
                atomic_json(root / 'receipts' / (key + '.json'), {'url': URL, 'sha256': sha, 'status': 200})
                row = profile_record(body.decode(), URL);row['pageEvidence'] = [{'url': URL, 'sha256': sha}]
                self.assertEqual(audit_rows(root, [row])[0]['professionalNameHint'], expected)
                page.write_bytes(body + b'changed')
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    audit_rows(root, [row])


if __name__ == '__main__':
    unittest.main()
