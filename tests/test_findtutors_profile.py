import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from directory_profiles import profile_record, profile_locality_status
from directory_profiles import Document
from findtutors_profile import advert_person_id
from qualification_context import profile_context_index
from discovery import atomic_json

URL = 'https://www.findtutors.co.uk/tutor/jane-example.htm'


def html(name='Jane Example', description='Jane Example, private teacher from Haddenham Buckinghamshire offers Maths classes', locations=''):
    return f'''<meta property="og:url" content="{URL}"><meta property="og:description" content="{description}">
        <nav>English tutors in High Wycombe</nav><div id="user-data"><h1>{name}</h1>
        <div id="user_clasesde">Classes of Maths, classes of 11 Plus</div></div>
        <h2>Profile of {name}</h2><div id="location_box">Location of my classes {locations}</div>
        <input id="contact_user_id" value="8175850">
        <aside><h2>Other tutors in London</h2><h1>Other Person</h1>Music in Buckinghamshire</aside>'''


class FindTutorsProfileTests(unittest.TestCase):
    def test_own_name_subjects_and_stable_person_id_do_not_come_from_slug_or_neighbours(self):
        row = profile_record(html(), URL)
        self.assertEqual(row['name'], 'Jane Example')
        self.assertEqual(row['personId'], '8175850')
        self.assertEqual(row['services'], ['11 Plus', 'Maths'])
        self.assertEqual(row['localityClaim'], 'Haddenham Buckinghamshire')
        self.assertEqual(profile_locality_status(row), 'advertised_bucks_locality')
        self.assertFalse(row['outreachEligible'])

    def test_online_is_not_local_without_own_class_locations(self):
        description = 'Jane Example, online tutor who teaches Maths online classes via the internet'
        row = profile_record(html(description=description), URL)
        self.assertEqual(profile_locality_status(row), 'unresolved')
        row = profile_record(html(description=description, locations='London · Marlow · High Wycombe'), URL)
        self.assertEqual(row['localityClaim'], 'Marlow')

    def test_foreign_canonical_ambiguous_ids_and_conflicting_own_names_are_rejected(self):
        for body in (html().replace(URL, URL.replace('jane-example', 'other-person')),
                     html() + '<input id="contact_user_id" value="999">',
                     html().replace('Profile of Jane Example', 'Profile of Other Person'),
                     html().replace('content="Jane Example, private', 'content="Other Person, private')):
            self.assertIsNone(profile_record(body, URL))

    def test_verified_context_replays_full_profile_fields_without_contact_form_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); key = hashlib.sha256(URL.encode()).hexdigest(); raw = html().encode()
            (root / 'pages').mkdir(); (root / 'pages' / (key + '.html')).write_bytes(raw)
            atomic_json(root / 'receipts' / (key + '.json'), {'url': URL, 'status': 200,
                'sha256': hashlib.sha256(raw).hexdigest()})
            page = profile_context_index([root], [{'website': URL}])[URL]
            self.assertEqual(page['extractionKind'], 'public_profile_fields_v2')
            self.assertEqual(json.loads(page['text'])['publicProfileFields']['personId'], '8175850')
            self.assertEqual(page['publicContacts']['emails'], [])
            (root / 'pages' / (key + '.html')).write_bytes(b'changed')
            self.assertEqual(profile_context_index([root], [{'website': URL}]), {})

    def test_advert_id_is_bound_separately_from_its_person_identity(self):
        url = 'https://www.findtutors.co.uk/tutors-high-wycombe/maths-teacher-7278275'
        body = f'''<meta property="og:url" content="{url}"><section id="sectionaddetail"></section>
            <input id="idad" value="7278275"><input id="iduser" value="8175850">
            <div id="report_error" data-u="8175850"></div>'''
        self.assertEqual(advert_person_id(Document(body).root, url), '8175850')
        for altered in (body.replace('value="7278275"', 'value="8175850"'),
                        body.replace('data-u="8175850"', 'data-u="999"'),
                        body.replace(url, url.replace('7278275', '123')), body + '<input id="iduser" value="999">'):
            self.assertIsNone(advert_person_id(Document(altered).root, url))
