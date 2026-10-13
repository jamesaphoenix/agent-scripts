import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
import directory_profiles as directory
import tta_scraper as tta


def profile(**changes):
    fields = {'First Name': 'Jane', 'Last Name': 'Example', 'City': 'Chalfont St. Peter',
              'County': 'Bucks', 'Company/Organisation Name': 'Jane Example Tuition',
              'Subject Specialisms': 'Mathematics, 11+ Maths, 11+ NVR',
              'Tuition Levels': 'KS1 (6-8 yrs), KS2 (8-11 yrs)'}
    fields.update(changes)
    return '<nav>London English Music</nav><div id="idMemberDirectoryDetailsContainer">' + ''.join(
        f'<div class="fieldContainer"><div class="fieldSubContainer"><div class="fieldLabel">{key}</div>'
        f'<div class="fieldBody"><span>{value}</span></div></div></div>' for key, value in fields.items()) + '</div><footer>help@example.org</footer>'


class TtaTests(unittest.TestCase):
    url = 'https://thetutorsassociation.wildapricot.org/Sys/PublicProfile/99590192/6119301'

    def test_own_public_fields_supply_identity_county_and_subjects(self):
        row = directory.profile_record(profile(), self.url)
        self.assertEqual((row['name'], row['personId']), ('Jane Example', '99590192'))
        self.assertEqual(row['localityClaim'], 'Chalfont St. Peter, Bucks')
        self.assertEqual(row['services'], ['Mathematics', '11+ Maths', '11+ NVR'])
        self.assertEqual(directory.profile_locality_status(row), 'advertised_bucks_locality')
        self.assertFalse(row['outreachEligible'])
        self.assertIsNone(row['email'])

    def test_empty_repeated_wildapricot_placeholders_do_not_hide_the_person(self):
        empty = '<div class="fieldContainer"><div class="fieldLabel"></div><div class="fieldBody"></div></div>'
        html = profile().replace('<div class="fieldContainer">', empty + '<div class="fieldContainer">')
        row = directory.profile_record(html, self.url)
        self.assertEqual(row['name'], 'Jane Example')
        self.assertIn('11+ NVR', row['services'])
        duplicate = '<div class="fieldContainer"><div class="fieldLabel">First Name</div><div class="fieldBody">Alex</div></div>'
        self.assertIsNone(directory.profile_record(html.replace('</div><footer>', duplicate + '</div><footer>'), self.url))

    def test_tta_cached_layout_replay_recovers_own_profile_without_requests_or_counter_reset(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = directory.PageCache(root)
            empty = '<div class="fieldContainer"><div class="fieldLabel"></div><div class="fieldBody"></div></div>'
            body = profile().replace('<div class="fieldContainer">', empty + '<div class="fieldContainer">').encode()
            page = cache.body_path(self.url)
            page.parent.mkdir();page.write_bytes(body)
            directory.save(root / 'receipts' / (cache.key(self.url) + '.json'), {'url': self.url, 'status': 200,
                'sha256': hashlib.sha256(body).hexdigest(), 'contentType': 'text/html', 'observedAt': '2026-10-13T20:00:00Z'})
            state = {'seen': [self.url], 'queue': [], 'records': [], 'attempts': 123, 'errors': []}
            with patch.object(directory.PageCache, 'download', side_effect=AssertionError('Replay must not fetch')):
                result = directory.replay_cached_profiles(root, state, 2000, clock=lambda:1000, platform='tta')
                repeated = directory.replay_cached_profiles(root, state, 2000, clock=lambda:1000, platform='tta')
            self.assertEqual(result['recoveredRecords'], 1)
            self.assertEqual(repeated['processedPages'], 1)
            self.assertEqual(len(state['records']), 1)
            self.assertEqual(state['attempts'], 123)
            self.assertEqual(state['records'][0]['name'], 'Jane Example')

    def test_other_county_and_subjects_cannot_borrow_bucks_directory_heading(self):
        row = directory.profile_record('<h1>Buckinghamshire Maths Tutors</h1>' + profile(
            **{'City': 'Reading', 'County': 'Berkshire', 'Subject Specialisms': 'Piano'}), self.url)
        self.assertEqual(directory.profile_locality_status(row), 'outside_or_unresolved')
        self.assertEqual(row['services'], ['Piano'])
        row = directory.profile_record(profile(**{'Subject Specialisms': 'Physics'}), self.url)
        self.assertEqual(row['services'], ['Physics'])
        self.assertNotIn('KS2 (8-11 yrs)', row['services'])

    def test_verified_outside_town_cannot_be_overridden_by_a_bucks_county_field(self):
        for city in ('Leighton Buzzard', 'Linslade', 'Wicken'):
            row = directory.profile_record(profile(**{'City': city, 'County': 'Bucks'}), self.url)
            self.assertEqual(directory.profile_locality_status(row), 'outside_or_unresolved')
        # Unknown local villages retain their explicit own county evidence.
        local = directory.profile_record(profile(**{'City': 'South Heath', 'County': 'Buckinghamshire'}), self.url)
        self.assertEqual(directory.profile_locality_status(local), 'advertised_bucks_locality')

    def test_own_primary_specialism_survives_the_academic_gate(self):
        row = directory.profile_record(profile(**{'Subject Specialisms': 'KS2'}), self.url)
        self.assertIsNone(directory.exclusion_reason(row))

    def test_public_fields_require_one_complete_person_and_reject_franchises(self):
        self.assertIsNone(directory.profile_record(profile(**{'Last Name': ''}), self.url))
        self.assertIsNone(directory.profile_record(profile() + profile(), self.url))
        row = directory.profile_record(profile(**{'Company/Organisation Name': 'Tutor Doctor Buckinghamshire'}), self.url)
        self.assertEqual(directory.exclusion_reason(row), 'institution_or_chain')
        row = directory.profile_record(profile(**{'Company/Organisation Name': 'Sherpa-Online'}), self.url)
        self.assertEqual(directory.exclusion_reason(row), 'institution_or_chain')

    def test_contact_fields_are_own_advertised_fields(self):
        row = directory.profile_record(profile(**{'Business Website': 'www.example-tuition.co.uk',
            'Business Email': 'jane@example-tuition.co.uk', 'Telephone': '01494 123456'}), self.url)
        self.assertEqual(row['website'], 'https://www.example-tuition.co.uk')
        self.assertEqual(row['email'], 'jane@example-tuition.co.uk')
        self.assertEqual(row['phone'], '01494 123456')
        self.assertNotIn('help@example.org', row['emails'])

    def test_only_published_public_profile_routes_are_admitted(self):
        self.assertEqual(directory.admitted_url(self.url + '?utm_source=x#enquire'), self.url)
        for path in ['/Sys/Login', '/Sys/MemberDirectory/LoadMembers?tab=1', '/Sys/PublicProfile/42/999',
                     '/Sys/PublicProfile/42/6119301/edit', '/Sys/Api/members']:
            self.assertIsNone(directory.admitted_url('https://thetutorsassociation.wildapricot.org' + path))

    def test_new_member_profile_with_an_existing_business_website_stays_held(self):
        from new_leads import classify_delta
        old = {'canonicalId': 'business:old', 'name': 'Example Tuition',
               'sourceUrls': ['http://example-tuition.co.uk/']}
        new = dict(directory.profile_record(profile(**{'Business Website': 'www.example-tuition.co.uk'}), self.url),
                   canonicalId='tutor:new')
        result = classify_delta([old], [new])
        self.assertEqual(result['new'], [])
        self.assertEqual(result['needsReview'][0]['deltaHoldReason'], 'possible_baseline_website_match')
        unrelated = dict(new, website='https://unrelated-tuition.co.uk')
        self.assertEqual(classify_delta([old], [unrelated])['new'], [unrelated])

    def index(self):
        return {'schemaVersion': 1, 'directoryUrl': tta.DIRECTORY_URL,
                'collection': 'public_rendered_directory', 'foundCount': 2,
                'firstObservedAt': '2026-10-13T20:00:00Z', 'lastObservedAt': '2026-10-13T20:00:10Z',
                'pageSize': 50, 'pages': [{'offset': 0, 'rows': 2}],
                'memberIds': ['99590192', '96268775'], 'priorityIds': ['99590192']}

    def test_complete_observed_index_publishes_idempotent_prioritised_seed_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / 'index.json'
            index.write_text(json.dumps(self.index()))
            first = tta.publish(index, root / 'profiles')
            second = tta.publish(index, root / 'profiles')
            self.assertEqual(first['batchSha256'], second['batchSha256'])
            path = root / 'profiles/seed-batches' / (first['batchSha256'] + '.json')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), path.stem)
            self.assertEqual(json.loads(path.read_text())['urls'][0], self.url)
            self.assertEqual(len(list(path.parent.glob('*.json'))), 1)

    def test_incomplete_duplicate_and_foreign_indexes_fail_closed(self):
        variants = [dict(self.index(), foundCount=3), dict(self.index(), memberIds=['99590192', '99590192']),
                    dict(self.index(), directoryUrl='https://other.example/'),
                    dict(self.index(), priorityIds=['123']), dict(self.index(), memberIds=['99590192', '../Login']),
                    dict(self.index(), pages=[{'offset': 50, 'rows': 2}])]
        for value in variants:
            with self.assertRaises(ValueError):
                tta.validate_index(value)

    def test_robot_request_rate_sets_host_spacing_without_slowing_other_sources(self):
        import urllib.robotparser
        robot = urllib.robotparser.RobotFileParser()
        robot.parse(['User-agent: *', 'Crawl-delay: 10', 'Request-rate: 1/60'])
        with tempfile.TemporaryDirectory() as tmp:
            cache = directory.PageCache(Path(tmp))
            cache.configure_robots(tta.HOST, robot)
            self.assertEqual(cache.host_spacing[tta.HOST], 60)
            self.assertEqual(cache.spacing, 2)

    def test_restart_retains_cooldown_and_does_not_dequeue_a_request_after_deadline(self):
        import urllib.robotparser
        robot = urllib.robotparser.RobotFileParser()
        robot.parse(['User-agent: *', 'Crawl-delay: 10', 'Request-rate: 1/60'])
        with tempfile.TemporaryDirectory() as tmp, patch('directory_profiles.time.time', return_value=120):
            root = Path(tmp)
            directory.save(root / 'request-times.json', {tta.HOST: 100})
            restarted = directory.PageCache(root)
            restarted.configure_robots(tta.HOST, robot)
            self.assertEqual(restarted.request_delay(self.url), 40)
            self.assertFalse(restarted.can_start_before(self.url, 150))
            self.assertTrue(restarted.can_start_before(self.url, 161))
            directory.save(root / 'receipts' / (restarted.key(self.url) + '.json'), {'error': 'HTTPError'})
            self.assertTrue(restarted.can_start_before(self.url, 150))
