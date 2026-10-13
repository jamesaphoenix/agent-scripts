"""A tutor card cannot borrow locality, teaching or contact details from a neighbour."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json
from professional_registers import BDA_URL, replay


def card(name, location, service, email):
    return f'<div class="listing-row"><h2>{name}</h2><span class="contact-info__icon--location">{location}</span><a href="mailto:{email}">{email}</a><div><h3>Age range</h3><ul><li>Primary</li></ul><h3>Services offered</h3><ul><li>{service}</li></ul></div></div>'


class RegisterTests(unittest.TestCase):
    def test_card_evidence_is_local_and_neighbouring_contacts_are_not_attributed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = (card('Jane Smith', 'Buckinghamshire, HP7', 'Literacy tuition', 'jane@example.net') +
                    card('Jane Jones', 'London, N1', 'Maths tuition', 'other@example.net') +
                    card('Alex Brown', 'Buckinghamshire, HP21', 'Full diagnostic assessment for dyslexia', 'assessor@example.net'))
            page = root / 'page.html'
            page.write_text(text)
            receipt = root / 'receipt.json'
            atomic_json(receipt, {'url': BDA_URL, 'status': 200, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest(), 'observedAt': '2026-10-13'})
            baseline = root / 'baseline.json'
            atomic_json(baseline, [])
            report = replay(page, receipt, baseline, root / 'review', root / 'candidates/bda.json')
            self.assertEqual(report['newResearchContacts'], 1)
            self.assertEqual(report['held'], 1)
            rows = json.loads((root / 'review/contacts.json').read_text())
            self.assertEqual(rows[0]['email'], 'jane@example.net')
            self.assertEqual(rows[0]['services'], ['Literacy tuition'])
            self.assertFalse(rows[0]['elevenPlusServiceClaim'])
            self.assertFalse(rows[0]['outreachEligible'])
            atomic_json(baseline, [{'canonicalId': 'known-source', 'name': 'Register search', 'sourceUrls': [BDA_URL]}])
            self.assertEqual(replay(page, receipt, baseline, root / 'review')['newResearchContacts'], 0)
            page.write_text(text + 'changed')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                replay(page, receipt, baseline, root / 'review')


if __name__ == '__main__':
    unittest.main()
