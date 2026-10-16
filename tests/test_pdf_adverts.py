"""Saved PDF adverts must replay their own complete, visually reviewed source unit."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json
from reviewed_contacts import replay
from pdf_adverts import MAX_PDF_BYTES, inspect_advert, pdf_advert_unit


def fixture_pdf(path):
    # Two separate adverts on one real PDF page, with distinct contact routes.
    stream = b'''BT /F1 12 Tf 30 260 Td (Jane Smith) Tj
0 -20 Td (I am based in Milton Keynes.) Tj
0 -20 Td (GCSE English tuition) Tj
0 -20 Td (jane@example.net) Tj
0 -20 Td (07700 900123) Tj ET
BT /F1 12 Tf 330 260 Td (Alex Jones) Tj
0 -20 Td (London maths tuition) Tj
0 -20 Td (alex@example.net) Tj ET'''
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 620 300] '
               b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    body, offsets = bytearray(b'%PDF-1.4\n'), [0]
    for i, value in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(str(i).encode() + b' 0 obj\n' + value + b'\nendobj\n')
    xref = len(body)
    body.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        body.extend(f'{offset:010} 00000 n \n'.encode())
    body.extend(b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n')
    path.write_bytes(body)


@unittest.skipUnless(all(shutil.which(t) for t in ('pdfinfo', 'pdftotext', 'pdftoppm')), 'Poppler required')
class PdfAdvertTests(unittest.TestCase):
    def fixture(self, root):
        page = root / 'public-source-review/parish/page.pdf'
        page.parent.mkdir(parents=True)
        fixture_pdf(page)
        receipt = page.with_name('receipt.json')
        atomic_json(receipt, {'url': 'https://parish.example.org/newsletter.pdf', 'status': 200,
                             'contentType': 'application/pdf', 'sha256': hashlib.sha256(page.read_bytes()).hexdigest()})
        item = inspect_advert(root, page, receipt, 1, [15, 15, 280, 160],
                              page.parent / 'jane-unit')
        item.update(name='Jane Smith', nameQuote='Jane Smith', localityClaim='Milton Keynes',
                    localityQuote='I am based in Milton Keynes.', services=['GCSE English tuition'],
                    email='jane@example.net', phone='07700 900123', reviewer='codex',
                    reviewedAt='2026-10-13T21:00:00+00:00')
        atomic_json(root / 'baseline/contacts.json', [])
        return page, receipt, item

    def replay(self, root, items):
        reviews = root / 'review/public-contact-reviews.json'
        atomic_json(reviews, items)
        return replay(root, reviews, root / 'baseline/contacts.json', root / 'review/public-contacts')

    def test_real_pdf_crop_preserves_own_claims_and_excludes_neighbour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, item = self.fixture(root)
            self.assertIn('Jane Smith', item['unitText'])
            self.assertNotIn('Alex Jones', item['unitText'])
            self.assertNotIn('alex@example.net', item['unitText'])
            self.assertEqual(self.replay(root, [item])['newResearchContacts'], 1)
            row = json.loads((root / 'review/public-contacts/new.json').read_text())[0]
            self.assertFalse(row['outreachEligible'])
            self.assertEqual(row['pageEvidence'][0]['pdfPage'], 1)
            self.assertEqual(row['pageEvidence'][0]['bounds'], item['bounds'])
            self.assertEqual(row['pageEvidence'][0]['visualReview'], item['visualReview'])

    def test_review_cannot_borrow_the_next_advert_or_clip_its_source_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, item = self.fixture(root)
            for bad in [dict(item, email='alex@example.net'),
                        dict(item, unitText='Jane Smith GCSE English tuition jane@example.net'),
                        dict(item, localityClaim='London'),
                        dict(item, bounds=[310, 15, 280, 160])]:
                with self.subTest(bad=bad):
                    self.assertEqual(self.replay(root, [bad])['held'], 1)

    def test_bad_document_does_not_stop_other_contacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, item = self.fixture(root)
            report = self.replay(root, [dict(item, pdfPage=2), item])
            self.assertEqual(report['held'], 1)
            self.assertEqual(report['newResearchContacts'], 1)

    def test_a_seventeen_megabyte_newsletter_keeps_original_body_and_advert_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, receipt, item = self.fixture(root)
            with page.open('ab') as stream:
                stream.write(b'\n%' + b' ' * 17000000 + b'\n')
            saved = json.loads(receipt.read_text())
            saved['sha256'] = hashlib.sha256(page.read_bytes()).hexdigest()
            atomic_json(receipt, saved)
            updated = inspect_advert(root, page, receipt, 1, item['bounds'], page.parent / 'large-jane-unit')
            for key in ('name', 'nameQuote', 'localityClaim', 'localityQuote', 'services',
                        'email', 'phone', 'reviewer', 'reviewedAt'):
                updated[key] = item[key]
            self.assertEqual(self.replay(root, [updated])['newResearchContacts'], 1)
            self.assertEqual(updated['visualReview']['pdfSha256'], saved['sha256'])
            self.assertNotIn('alex@example.net', updated['unitText'])

    def test_documents_above_the_finite_pdf_limit_are_rejected_before_poppler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, _, item = self.fixture(root)
            with page.open('r+b') as stream:
                stream.truncate(MAX_PDF_BYTES + 1)
            with self.assertRaisesRegex(ValueError, 'bounded public PDF'):
                pdf_advert_unit(page, item)

    def test_missing_or_changed_visual_evidence_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, item = self.fixture(root)
            missing = dict(item)
            missing.pop('visualReview')
            self.assertEqual(self.replay(root, [missing])['held'], 1)
            image = root / item['visualReview']['image']
            image.write_bytes(image.read_bytes() + b'changed')
            self.assertEqual(self.replay(root, [item])['held'], 1)

    def test_receipt_and_document_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, receipt, item = self.fixture(root)
            changed = json.loads(receipt.read_text())
            changed['contentType'] = 'text/html'
            atomic_json(receipt, changed)
            bad = dict(item, receiptSha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
            self.assertEqual(self.replay(root, [bad])['held'], 1)
            changed['contentType'] = 'application/pdf'
            atomic_json(receipt, changed)
            page.write_bytes(page.read_bytes() + b'changed')
            bad['receiptSha256'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
            self.assertEqual(self.replay(root, [bad])['held'], 1)

    def test_unbounded_or_non_integer_page_and_rectangle_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, _, item = self.fixture(root)
            for bad in [dict(item, pdfPage=True), dict(item, pdfPage=0), dict(item, pdfPage=501),
                        dict(item, bounds=[0, 0, -1, 10]), dict(item, bounds=[0, 0, 621, 300]),
                        dict(item, bounds=[0, 0, 620, 301]), dict(item, bounds=[True, 0, 10, 10]),
                        dict(item, bounds=[0.5, 0, 10, 10]), dict(item, bounds=[0, 0, 10])]:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    pdf_advert_unit(page, bad)

    def test_evidence_paths_cannot_escape_the_public_source_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, receipt, item = self.fixture(root)
            outside = root / 'outside.pdf'
            outside.write_bytes(page.read_bytes())
            self.assertEqual(self.replay(root, [dict(item, page='outside.pdf')])['held'], 1)
            with self.assertRaises(ValueError):
                inspect_advert(root, outside, receipt, 1, item['bounds'], root / 'other-unit')

    def test_inspection_does_not_replace_its_source_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, receipt, item = self.fixture(root)
            original = receipt.read_bytes()
            with self.assertRaises(ValueError):
                inspect_advert(root, page, receipt, 1, item['bounds'], receipt.with_suffix(''))
            self.assertEqual(receipt.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
