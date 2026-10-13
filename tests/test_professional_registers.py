"""A tutor card cannot borrow locality, teaching or contact details from a neighbour."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json
from professional_registers import BDA_URL, parse_bda, replay


def card(name, location, service, email):
    return f'<div class="listing-row"><h2>{name}</h2><span class="contact-info__icon--location">{location}</span><a href="mailto:{email}">{email}</a><div><h3>Age range</h3><ul><li>Primary</li></ul><h3>Services offered</h3><ul><li>{service}</li></ul></div></div>'


class RegisterTests(unittest.TestCase):
    def test_same_name_with_different_contact_routes_is_held_for_identity_review(self):
        text = (card('Jane Smith', 'Buckinghamshire, HP7', 'Literacy tuition', 'first@example.net') +
                card('Jane Smith', 'Buckinghamshire, HP7', 'Maths tuition', 'second@example.net'))
        candidates, held = parse_bda(text, {'url': BDA_URL, 'status': 200, 'sha256': 'fixture', 'observedAt': 'fixture'})
        self.assertEqual(candidates, [])
        self.assertEqual(len(held), 2)

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
            from campaign_watch import aggregate_contacts
            # A directory profile with this name already found in the same run
            # must prevent a second register contact inflating the total.
            atomic_json(root / 'baseline/contacts.json', [])
            atomic_json(root / 'public-source-review/bda/receipt.json', json.loads(receipt.read_text()))
            (root / 'public-source-review/bda/page.html').write_bytes(page.read_bytes())
            atomic_json(root / 'profile-audit/this-stage-delta/new.json', [{'canonicalId': 'profile:jane',
                'name': 'Jane Smith', 'sourceUrls': ['https://tutor.example.net/jane'], 'outreachEligible': False}])
            aggregate = aggregate_contacts(root, {'outputDirectory': str(root / 'profile-audit')})
            self.assertEqual(aggregate['delta']['newAcceptedResearchIdentities'], 1)
            self.assertEqual(len(json.loads((root / 'public-source-review/bda/contacts/current-run-needs-review.json').read_text())), 1)
            atomic_json(baseline, [{'canonicalId': 'known-source', 'name': 'Register search', 'sourceUrls': [BDA_URL]}])
            self.assertEqual(replay(page, receipt, baseline, root / 'review')['newResearchContacts'], 0)
            page.write_text(text + 'changed')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                replay(page, receipt, baseline, root / 'review')


if __name__ == '__main__':
    unittest.main()
