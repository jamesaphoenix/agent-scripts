import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from directory_profiles import annotate_advert, public_person, profile_record
from qualification_context import profile_context_index


class ProfileOfferExcerptTests(unittest.TestCase):
    def test_only_supplied_own_description_is_preserved_and_bounded(self):
        person = public_person('example', '1', 'Jane Smith', 'Amersham', ['Maths'], 'https://example.org/1')
        text = 'I teach primary maths in Amersham. ' * 200
        row = annotate_advert(person, title='Navigation: piano in London', description=text)
        self.assertEqual(row['ownOfferExcerpt'], text.strip()[:2000])
        self.assertNotIn('piano', row['ownOfferExcerpt'])
        self.assertNotIn('ownOfferExcerpt', annotate_advert(dict(person, evidenceKind='directory_listing'), description='Another tutor.'))

    def test_empty_descriptions_do_not_manufacture_context(self):
        person = public_person('example', '1', 'Jane Smith', 'Amersham', ['Maths'], 'https://example.org/1')
        self.assertNotIn('ownOfferExcerpt', annotate_advert(person, title='Maths tutor', description=' '))

    def test_own_offer_survives_replay_but_neighbour_bio_does_not(self):
        from test_teachtutti import profile, URL
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'pages').mkdir(); (root / 'receipts').mkdir()
            body = (f'<link rel="canonical" href="{URL}">' + profile(bio='I teach maths in Milton Keynes.')).encode()
            key = hashlib.sha256(URL.encode()).hexdigest()
            (root / 'pages' / (key + '.html')).write_bytes(body)
            (root / 'receipts' / (key + '.json')).write_text(json.dumps({
                'url': URL, 'status': 200, 'sha256': hashlib.sha256(body).hexdigest()}))
            index = profile_context_index([root], [{'website': URL}])
            fields = json.loads(index[URL]['text'])['publicProfileFields']
            self.assertEqual(fields['ownOfferExcerpt'], 'I teach maths in Milton Keynes.')
            self.assertNotIn('London', fields['ownOfferExcerpt'])
            self.assertNotIn('ownRoleQuote', fields)
