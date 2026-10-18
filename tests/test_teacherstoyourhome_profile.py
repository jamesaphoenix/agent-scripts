import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from directory_profiles import admitted_url, exclusion_reason, profile_locality_status, profile_record


class TeachersToYourHomeProfileTests(unittest.TestCase):
    URL = 'https://www.teacherstoyourhome.com/uk/tutor-near-me/29048'

    def test_direct_local_teacher_profile_keeps_platform_identity_and_offer(self):
        page = '''<h1>Miss Kathryn O</h1><h2>Tutor in Aylesbury, Buckinghamshire</h2>
          <div><h2>About me</h2><div>I am a primary school teacher offering tutoring in KS1 and KS2.</div></div>
          <div><h2>Subjects offered</h2><div><table>
            <tr><th>Subject (and level)</th><th>Price</th></tr>
            <tr><td>Primary (Key Stage 1)</td><td>£53</td></tr>
            <tr><td>Primary (Key Stage 2)</td><td>£53</td></tr>
          </table></div></div>'''
        row = profile_record(page, self.URL)
        self.assertEqual((row['personId'], row['name']), ('29048', 'Kathryn O'))
        self.assertEqual(row['services'], ['Primary (Key Stage 1)', 'Primary (Key Stage 2)'])
        self.assertEqual(profile_locality_status(row), 'advertised_bucks_locality')
        self.assertIsNone(exclusion_reason(row))
        self.assertFalse(row['outreachEligible'])
        self.assertTrue(row['ownedProfileFields'])

    def test_only_bounded_public_profile_paths_are_admitted(self):
        self.assertEqual(admitted_url(self.URL), self.URL)
        for url in (
            'https://www.teacherstoyourhome.com/uk/location-tutor/aylesbury',
            self.URL + '/contact',
            self.URL.replace('https://', 'http://'),
            'https://www.teacherstoyourhome.com/uk/tutor-near-me/0',
        ):
            self.assertIsNone(admitted_url(url))

    def test_missing_owner_fields_and_non_local_claim_do_not_create_local_tutor(self):
        page = '''<h1>Miss Kathryn O</h1><h2>Tutor in Oxford, Oxfordshire</h2>
          <div><h2>About me</h2><div>I tutor maths.</div></div>
          <div><h2>Subjects offered</h2><table><tr><td>Maths</td></tr></table></div>'''
        self.assertIsNone(profile_record(page, self.URL))
        self.assertIsNone(profile_record('<h1>Miss Kathryn O</h1>', self.URL))

    def test_exact_town_only_headers_within_existing_geography(self):
        for town, expected in (
            ('Chalfont St Giles', 'Chalfont St Giles, Buckinghamshire'),
            ('Chalfont St Peter', 'Chalfont St Peter, Buckinghamshire'),
            ('Newton Leys', 'Milton Keynes'),
        ):
            with self.subTest(town=town):
                page = f'''<h1>Ms Lauren H</h1><h2>Tutor in {town}</h2>
                  <div><h2>About me</h2><p>I provide primary tutoring.</p></div>
                  <div><h2>Subjects offered</h2><table><tr><td>Primary (Key Stage 2)</td></tr></table></div>'''
                row = profile_record(page, self.URL)
                self.assertEqual(row['localityClaim'], expected)
                self.assertEqual(profile_locality_status(row), 'advertised_bucks_locality')
        for heading in ('Tutor in Oxford', 'Tutor in Newton Leys, Bedfordshire'):
            page = f'''<h1>Ms Lauren H</h1><h2>{heading}</h2>
              <div><h2>About me</h2><p>I provide primary tutoring.</p></div>
              <div><h2>Subjects offered</h2><table><tr><td>Primary (Key Stage 2)</td></tr></table></div>'''
            self.assertIsNone(profile_record(page, self.URL))


if __name__ == '__main__':
    unittest.main()
