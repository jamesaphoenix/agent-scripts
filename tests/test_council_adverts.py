import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json
from reviewed_contacts import council_advert_unit, replay


class CouncilAdvertTests(unittest.TestCase):
    def fixture(self, root):
        record = {'id': 7, 'status': 'active', 'name': 'GCSE English tuition',
            'description': 'Jane Smith offers GCSE English tuition in Haddenham, Buckinghamshire.',
            'contacts': [{'name': 'Jane Smith', 'email': 'jane@example.net', 'phone': '07700 900123'}],
            'locations': [{'city': 'Haddenham'}]}
        neighbour = {'id': 8, 'status': 'active', 'name': 'Alex Jones',
                     'description': 'Tuition in London', 'contacts': [{'email': 'alex@example.net'}]}
        page = root / 'public-source-review/council.json'
        atomic_json(page, {'content': [record, neighbour]})
        receipt = root / 'public-source-review/council.receipt.json'
        atomic_json(receipt, {'status': 200, 'sha256': hashlib.sha256(page.read_bytes()).hexdigest(),
            'url': 'https://api.familyinfo.buckinghamshire.gov.uk/api/v1/services?keywords=tutor',
            'contentType': 'application/json', 'observedAt': '2026-10-13'})
        text, _ = council_advert_unit(json.loads(page.read_text()), 7)
        item = {'sourceType': 'council_advert', 'sourceKind': 'public_advert', 'recordId': 7,
            'page': 'public-source-review/council.json', 'receipt': 'public-source-review/council.receipt.json',
            'receiptSha256': hashlib.sha256(receipt.read_bytes()).hexdigest(), 'unitText': text,
            'name': 'Jane Smith', 'nameQuote': 'Jane Smith', 'localityClaim': 'Haddenham',
            'localityQuote': 'Haddenham, Buckinghamshire.', 'services': ['GCSE English tuition'],
            'email': 'jane@example.net', 'phone': '07700 900123',
            'reviewer': 'codex', 'reviewedAt': '2026-10-13'}
        atomic_json(root / 'baseline.json', [])
        return page, receipt, item

    def review(self, root, items):
        path = root / 'reviews.json'
        atomic_json(path, items)
        return replay(root, path, root / 'baseline.json', root / 'output')

    def test_complete_record_has_a_specific_public_service_link_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, item = self.fixture(root)
            self.assertEqual(self.review(root, [item])['newResearchContacts'], 1)
            row = json.loads((root / 'output/new.json').read_text())[0]
            self.assertEqual(row['sourceUrls'], ['https://directory.familyinfo.buckinghamshire.gov.uk/service/7'])
            self.assertEqual(row['pageEvidence'][0]['recordId'], 7)
            self.assertEqual(len(row['pageEvidence'][0]['recordSha256']), 64)
            self.assertFalse(row['outreachEligible'])

    def test_neighbour_contact_and_cherry_picked_unit_are_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, item = self.fixture(root)
            for bad in [dict(item, email='alex@example.net'), dict(item, unitText='Jane Smith GCSE English tuition Haddenham jane@example.net')]:
                self.assertEqual(self.review(root, [bad])['held'], 1)

    def test_ambiguous_inactive_and_unbounded_records_are_rejected(self):
        record = {'id': 7, 'status': 'active', 'description': 'Jane Smith tuition'}
        for value, key in [({'content': [record, record]}, 7),
                           (dict(record, status='inactive'), 7),
                           (record, True), (record, -1),
                           ({'content': [record] * 101}, 7), ({'content': [record]}, 8)]:
            with self.subTest(value=str(value)[:70], key=key), self.assertRaises(ValueError):
                council_advert_unit(value, key)

    def test_tampered_raw_body_is_held_without_stopping_a_good_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, _, item = self.fixture(root)
            self.assertEqual(self.review(root, [dict(item, receiptSha256='f' * 64), item])['newResearchContacts'], 1)
            page.write_text('{}')
            self.assertEqual(self.review(root, [item])['held'], 1)

    def test_unsupported_api_or_mismatched_single_record_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, receipt, item = self.fixture(root)
            original = json.loads(receipt.read_text())
            for url in ['https://example.org/api/v1/services',
                        'https://api.familyinfo.buckinghamshire.gov.uk/api/v1/services/8']:
                atomic_json(receipt, dict(original, url=url))
                changed = dict(item, receiptSha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
                self.assertEqual(self.review(root, [changed])['held'], 1)


if __name__ == '__main__':
    unittest.main()
