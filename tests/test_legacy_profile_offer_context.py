"""Replay own offer evidence without borrowing another directory member's fields."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from directory_profiles import profile_record
from qualification_context import profile_context_index

CHILD = 'https://www.childcare.co.uk/profile/111'
HUNT = 'https://www.tutorhunt.com/users/123456/'


def child_profile(identifier='111', town='Milton Keynes', subject='English Tutor', name='Jane'):
    return f'''<div data-service="Private Tutor" data-user-id="{identifier}">
        <div class="profile-header--info-wrapper"><h1>{name}</h1><h3>Private Tutor in {town}</h3></div>
        <div class="tag positive">{subject}</div>
        <div><h3>About Me</h3><p>I offer English lessons in {town}.</p></div></div>'''


def hunt_profile():
    return '''<div id="profilenameinfo"><span id="profilenametxt">Jane</span>
        <div id="profileloc">Amersham, Buckinghamshire</div></div>
        <table class="profilesubjectstattable"><tr><td class="marksub">Maths</td></tr></table>
        <div class="card"><h2>Information about Jane</h2>I offer GCSE maths tuition in Amersham.</div>'''


class LegacyProfileOfferContextTests(unittest.TestCase):
    def replay(self, url, html, canonical=True):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'pages').mkdir(); (root / 'receipts').mkdir()
            if canonical:
                html = f'<link rel="canonical" href="{url}">' + html
            raw = html.encode(); key = hashlib.sha256(url.encode()).hexdigest()
            (root / 'pages' / (key + '.html')).write_bytes(raw)
            (root / 'receipts' / (key + '.json')).write_text(json.dumps({
                'url': url, 'status': 200, 'sha256': hashlib.sha256(raw).hexdigest()}))
            return profile_context_index([root], [{'website': url}])

    def test_childcare_own_container_bounds_subjects_bio_and_literal_role(self):
        html = child_profile() + child_profile('222', 'London', 'Music Tutor', 'Other Tutor')
        row = profile_record(html, CHILD)
        self.assertEqual(row['services'], ['English Tutor'])
        page = self.replay(CHILD, html)[CHILD]
        fields = json.loads(page['text'])['publicProfileFields']
        self.assertEqual(page['extractionKind'], 'public_profile_fields_v2')
        self.assertEqual(fields['ownRoleQuote'], 'Private Tutor in Milton Keynes')
        self.assertEqual(fields['ownOfferExcerpt'], 'I offer English lessons in Milton Keynes.')
        self.assertNotIn('London', page['text'])
        self.assertNotIn('Other Tutor', page['text'])

    def test_tutorhunt_replays_only_the_matching_information_card(self):
        html = hunt_profile() + '<div class="card"><h2>Information about Other Tutor</h2>Piano in London.</div>'
        page = self.replay(HUNT, html)[HUNT]
        self.assertEqual(page['extractionKind'], 'public_profile_fields_v2')
        self.assertIn('I offer GCSE maths tuition in Amersham.', page['text'])
        self.assertNotIn('Piano', page['text'])
        self.assertNotIn('London', page['text'])

    def test_upgraded_context_requires_its_own_canonical_profile(self):
        for url, html, foreign in [(CHILD, child_profile(), CHILD.replace('111', '222')),
                                   (HUNT, hunt_profile(), HUNT.replace('123456', '654321'))]:
            with self.subTest(url=url):
                self.assertEqual(self.replay(url, html, canonical=False), {})
                mismatch = f'<link rel="canonical" href="{foreign}">' + html
                self.assertEqual(self.replay(url, mismatch, canonical=False), {})

    def test_ambiguous_childcare_owners_and_tutorhunt_headers_fail_closed(self):
        self.assertIsNone(profile_record(child_profile() + child_profile(name='Another'), CHILD))
        self.assertIsNone(profile_record(hunt_profile() + '<div id="profilenameinfo"><h1>Other</h1></div>', HUNT))

    def test_unscoped_legacy_markup_does_not_gain_own_offer_evidence(self):
        html = child_profile().replace('data-user-id="111"', '')
        page = self.replay(CHILD, html)[CHILD]
        self.assertEqual(page['extractionKind'], 'public_profile_fields_v1')
        self.assertNotIn('ownOfferExcerpt', page['text'])
        self.assertNotIn('ownRoleQuote', page['text'])

    def test_another_childcare_member_cannot_supply_the_requested_identity(self):
        html = child_profile().replace('data-user-id="111"', 'data-user-id="222"')
        self.assertIsNone(profile_record(html, CHILD))
        self.assertEqual(self.replay(CHILD, html), {})

    def test_identical_responsive_subject_table_can_be_replayed(self):
        table = '<table class="profilesubjectstattable"><tr><td class="marksub">Maths</td></tr></table>'
        html = hunt_profile() + table
        page = self.replay(HUNT, html)[HUNT]
        self.assertEqual(page['extractionKind'], 'public_profile_fields_v2')
        self.assertIn('GCSE maths tuition in Amersham', page['text'])

    def test_repeated_tutorhunt_subject_tables_do_not_gain_new_context(self):
        html = hunt_profile() + '<table class="profilesubjectstattable"><tr><td class="marksub">Music</td></tr></table>'
        page = self.replay(HUNT, html)[HUNT]
        self.assertEqual(page['extractionKind'], 'public_profile_fields_v1')
        self.assertNotIn('ownOfferExcerpt', page['text'])


if __name__ == '__main__': unittest.main()
