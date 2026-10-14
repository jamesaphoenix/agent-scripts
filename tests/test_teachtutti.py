import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from directory_profiles import admitted_url, exclusion_reason, listing_records, listing_profile_urls, local_directory_link, profile_record
from audit_directory_identities import audit_rows

HOST = 'https://www.teachtutti.co.uk'
URL = HOST + '/tutor/branavi-y-maths-biology-milton-keynes/'


def profile(name='Branavi', town='Milton Keynes', subject='Maths (GCSE)', bio='I tutor maths.'):
    return f'''<nav>English and 11+ tutors in Buckinghamshire</nav>
        <div id="npc1"><h1><div class="tn1">{name}</div></h1><span class="ls3">{town}</span></div>
        <div class="pb3 sc2"><div class="bt3"><h2>Subjects taught</h2></div><table class="stt1"><tr><th>Subject</th></tr>
        <tr><td>{subject}</td><td>Beginner</td></tr></table></div>
        <div class="pb3"><div class="bt3"><h2>Welcome from {name}</h2></div><div itemprop="description">{bio}</div></div>
        <aside><h2>Welcome from Other Tutor</h2><div itemprop="description">Based in London</div>
        <table><tr><td>English</td><td>Eleven Plus (11+)</td></tr></table></aside>'''


class TeachTuttiTests(unittest.TestCase):
    def test_directory_members_do_not_share_business_or_reward_identity(self):
        from discovery import hold_reason, professional_profile, website_key
        other = HOST + '/tutor/folahan-a-architecture-photoshop-miltonkeynes/'
        self.assertTrue(professional_profile(URL))
        self.assertNotEqual(website_key(URL), website_key(other))
        self.assertEqual(hold_reason({'name': 'TeachTutti', 'website': HOST + '/tutor-list/maths/'}),
                         'source_directory_or_job_page')

    def test_public_paths_exclude_search_messages_and_unbounded_directory_expansion(self):
        seed = HOST + '/tutor-list/maths-gcse/'
        self.assertEqual(admitted_url(URL + '?utm_source=listing#contact'), URL)
        self.assertEqual(admitted_url(seed), seed)
        self.assertEqual(admitted_url(HOST + '/robots.txt'), HOST + '/robots.txt')
        for path in ('/search/', '/crm/', '/hub-student-messages/', '/wp-login.php', '/tutor/', '/tutor/branavi/reviews/'):
            self.assertIsNone(admitted_url(HOST + path))
        self.assertFalse(local_directory_link(URL, seed))
        self.assertFalse(local_directory_link(HOST + '/tutor-list/physics/', seed))
        self.assertTrue(local_directory_link(seed, seed))

    def test_own_profile_fields_ignore_page_heading_and_neighbouring_tutors(self):
        row = profile_record(profile(), URL)
        self.assertIsNotNone(row)
        self.assertEqual(row['name'], 'Branavi')
        self.assertEqual(row['localityClaim'], 'Milton Keynes')
        self.assertEqual(row['services'], ['Maths (GCSE)'])
        self.assertEqual(row['personId'], hashlib.sha256(URL.encode()).hexdigest())
        self.assertIsNone(exclusion_reason(row))
        self.assertFalse(row['outreachEligible'])
        outside = profile_record(profile(town='London'), URL)
        self.assertEqual(outside['localityClaim'], 'London')
        self.assertIsNone(profile_record(profile().replace('id="npc1"', 'id="related"'), URL))
        self.assertIsNone(profile_record(profile().replace('Subjects taught', 'Other tutors'), URL))

    def test_own_biography_locality_conflict_stays_held(self):
        row = profile_record(profile(town='Chesham', bio='Based in London I would be happy to travel.'), URL)
        self.assertTrue(row['locationConflict'])
        self.assertEqual(row['additionalLocalityClaims'], ['London'])
        row = profile_record(profile(bio='I live in Milton Keynes and teach online.'), URL)
        self.assertFalse(row.get('locationConflict', False))

    def test_all_numbered_cards_keep_their_own_location_and_subjects(self):
        def card(index, name, town, subject):
            return f'''<a class="tt_{index} tpt9 trsr1" href="/tutor/{name.lower()}-maths/">
                <div class="tcn3">{name}</div><span class="tlpr-0">{town}</span>
                <span class="mths3">{subject}</span><div class="i9">I teach.</div></a>'''
        seed = HOST + '/tutor-list/maths-gcse/'
        cards = listing_records(card(3, 'Robert', 'Amersham', 'Maths') + card(343, 'Branavi', 'Milton Keynes', 'Maths (GCSE)')
            + card(352, 'National', 'London', 'English'), seed)
        self.assertEqual([x['name'] for x in cards], ['Robert', 'Branavi', 'National'])
        self.assertEqual(listing_profile_urls(cards, 'www.teachtutti.co.uk'),
                         [HOST + '/tutor/robert-maths/', HOST + '/tutor/branavi-maths/'])
        self.assertTrue(all(x['evidenceKind'] == 'directory_listing' for x in cards))

    def test_hash_bound_own_profile_can_enter_the_existing_identity_audit(self):
        body = profile().encode()
        digest, key = hashlib.sha256(body).hexdigest(), hashlib.sha256(URL.encode()).hexdigest()
        row = profile_record(body.decode(), URL)
        self.assertIsNotNone(row)
        row['pageEvidence'] = [{'url': URL, 'sha256': digest}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'receipts').mkdir()
            (root / 'pages').mkdir()
            page = root / 'pages' / (key + '.html')
            page.write_bytes(body)
            (root / 'receipts' / (key + '.json')).write_text(json.dumps({'url': URL, 'sha256': digest}))
            self.assertEqual(audit_rows(root, [row])[0]['name'], 'Branavi')
            page.write_bytes(body.replace(b'Milton Keynes', b'London'))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                audit_rows(root, [row])
