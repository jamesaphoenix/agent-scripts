import hashlib
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import sqlite3
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json
from reviewed_contacts import replay
from directory_profiles import Document


class HtmlAdvertUnitTests(unittest.TestCase):
    def test_source_unit_without_an_id_has_a_complete_bounded_dom_path(self):
        from reviewed_contacts import html_advert_unit
        doc = Document('<main><article>Jane: piano lessons in Milton Keynes.</article><article>Alex: London.</article></main>')
        text, proof = html_advert_unit(doc, {'unitPath': [0, 0]})
        self.assertEqual(text, 'Jane: piano lessons in Milton Keynes.')
        self.assertEqual(proof, {'unitPath': [0, 0]})

    def test_contiguous_sibling_range_excludes_the_next_adverts_contact(self):
        from reviewed_contacts import html_advert_unit
        doc = Document('<main id="ads"><h2>Jane Smith</h2><p>Milton Keynes piano lessons</p><p>07700 900123</p><h2>Alex Jones</h2><p>alex@example.net</p></main>')
        text, proof = html_advert_unit(doc, {'unitId': 'ads', 'childRange': [0, 3]})
        self.assertEqual(text, 'Jane Smith Milton Keynes piano lessons 07700 900123')
        self.assertNotIn('alex@example.net', text)
        self.assertEqual(proof['childRange'], [0, 3])

    def test_ambiguous_or_unbounded_selectors_are_rejected(self):
        from reviewed_contacts import html_advert_unit
        doc = Document('<main id="ads"><p>Jane</p></main>')
        for bad in [{}, {'unitId': 'ads', 'unitPath': [0]}, {'unitPath': [False]},
                    {'unitPath': [-1]}, {'unitPath': [5]}, {'unitPath': [0] * 21},
                    {'unitId': 'ads', 'childRange': [0, 65]}, {'unitId': 'ads', 'childRange': [1, 0]},
                    {'unitId': 'ads', 'childRange': [False, 1]}, {'unitId': 'ads', 'childRange': [0, 2]}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                html_advert_unit(doc, bad)

    def test_repeated_html_ids_remain_ambiguous(self):
        from reviewed_contacts import html_advert_unit
        with self.assertRaises(ValueError):
            html_advert_unit(Document('<p id="ads">Jane</p><p id="ads">Alex</p>'), {'unitId': 'ads'})


class ReviewedContactTests(unittest.TestCase):
    def browser_fixture(self, root):
        _, item = self.fixture(root)
        text = item['unitText']
        page = root / 'public-source-review/browser/rendered.txt'
        page.parent.mkdir(parents=True)
        page.write_text(text)
        receipt = page.with_name('receipt.json')
        atomic_json(receipt, {'url': 'https://tutor.example.org/', 'observationType': 'browser_rendered_text',
            'sha256': hashlib.sha256(page.read_bytes()).hexdigest(), 'observedAt': item['reviewedAt']})
        item.update(sourceType='browser_advert', sourceKind='own_site', website='https://tutor.example.org/',
            page=str(page.relative_to(root)), receipt=str(receipt.relative_to(root)),
            receiptSha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
        return page, receipt, item

    def test_rendered_browser_advert_retains_its_source_kind_without_claiming_http_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, item = self.browser_fixture(root)
            report = self.run_review(root, [item])
            self.assertEqual(report['newResearchContacts'], 1)
            row = json.loads((root / 'review/public-contacts/contacts.json').read_text())[0]
            self.assertEqual(row['email'], 'jane@example.net')
            self.assertEqual(row['pageEvidence'][0]['observationType'], 'browser_rendered_text')
            self.assertNotIn('status', row['pageEvidence'][0])
            self.assertFalse(row['outreachEligible'])

    def test_browser_advert_requires_complete_owned_snapshot_and_matching_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page, _, item = self.browser_fixture(root)
            for bad in (dict(item, sourceKind='public_advert'), dict(item, unitText='Jane Smith GCSE English tuition.')):
                self.assertEqual(self.run_review(root, [bad])['held'], 1)
            page.write_text(item['unitText'].replace('Haddenham', 'London'))
            self.assertEqual(self.run_review(root, [item])['held'], 1)

    def test_browser_receipt_cannot_be_relabelled_as_an_http_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, receipt, item = self.browser_fixture(root)
            value = json.loads(receipt.read_text())
            for invalid in (dict(value, observationType='http_response', status=200), dict(value, status=200)):
                atomic_json(receipt, invalid)
                bad = dict(item, receiptSha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
                self.assertEqual(self.run_review(root, [bad])['held'], 1)

    def fixture(self, root):
        text = 'Jane Smith offers GCSE English tuition. I am based in Haddenham, Buckinghamshire. Contact jane@example.net or 07700 900123.'
        path = root / 'crawls/public-directory/crawl.json'
        atomic_json(path, {'results': [{'node': {'url': 'https://village.example.org/services'},
            'evidence': {'url': 'https://village.example.org/services', 'text': text,
                         'contentHash': 'a' * 64, 'emails': ['jane@example.net']}}], 'updatedAt': '2026-10-13'})
        item = {'checkpoint': 'crawls/public-directory/crawl.json',
            'checkpointSha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'resultIndex': 0,
            'sourceKind': 'public_advert', 'unitText': text, 'name': 'Jane Smith',
            'nameQuote': 'Jane Smith', 'localityClaim': 'Haddenham',
            'localityQuote': 'I am based in Haddenham, Buckinghamshire.',
            'services': ['GCSE English tuition'], 'email': 'jane@example.net', 'phone': '07700 900123',
            'reviewer': 'codex', 'reviewedAt': '2026-10-13T15:00:00+00:00'}
        atomic_json(root / 'baseline/contacts.json', [])
        return path, item

    def run_review(self, root, items):
        p = root / 'review/public-contact-reviews.json'
        atomic_json(p, items)
        return replay(root, p, root / 'baseline/contacts.json', root / 'review/public-contacts')

    def test_verified_advert_exports_its_advertised_contact_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            report = self.run_review(root, [item])
            self.assertEqual(report['newResearchContacts'], 1)
            row = json.loads((root / 'review/public-contacts/contacts.json').read_text())[0]
            self.assertEqual(row['email'], 'jane@example.net')
            self.assertFalse(row['outreachEligible'])
            self.assertEqual(row['pageEvidence'][0]['checkpointSha256'], item['checkpointSha256'])

    def test_hashed_html_sibling_advert_replays_without_borrowing_neighbour_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            body = ('<main><h2>Jane Smith</h2><p>I am based in Haddenham, Buckinghamshire.</p>'
                    '<p>GCSE English tuition. Contact 07700 900123.</p>'
                    '<h2>Alex Jones</h2><p>alex@example.net</p></main>').encode()
            page = root / 'public-source-review/list/page.html'
            page.parent.mkdir(parents=True)
            page.write_bytes(body)
            receipt = page.with_name('receipt.json')
            atomic_json(receipt, {'status': 200, 'url': 'https://village.example.org/services',
                                 'sha256': hashlib.sha256(body).hexdigest()})
            item.update(sourceType='html_advert', page=str(page.relative_to(root)),
                        receipt=str(receipt.relative_to(root)), receiptSha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
                        unitPath=[0], childRange=[0, 3], email=None,
                        unitText='Jane Smith I am based in Haddenham, Buckinghamshire. GCSE English tuition. Contact 07700 900123.')
            self.assertEqual(self.run_review(root, [item])['newResearchContacts'], 1)
            self.assertEqual(self.run_review(root, [dict(item, email='alex@example.net')])['held'], 1)
            self.assertEqual(self.run_review(root, [dict(item, unitText='Jane Smith GCSE English tuition. Contact 07700 900123.')])['held'], 1)

    def test_one_bad_or_tampered_item_does_not_stop_other_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            bad = dict(item, checkpointSha256='b' * 64)
            report = self.run_review(root, [bad, item])
            self.assertEqual(report['held'], 1)
            self.assertEqual(report['newResearchContacts'], 1)

    def test_advert_cannot_take_neighbour_contact_or_unproved_locality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            for bad in [dict(item, email='neighbour@example.net'),
                        dict(item, localityClaim='London'),
                        dict(item, localityQuote='Based in London'),
                        dict(item, name='Other Tutor')]:
                with self.subTest(bad=bad):
                    report = self.run_review(root, [bad])
                    self.assertEqual(report['newResearchContacts'], 0)
                    self.assertEqual(report['held'], 1)

    def test_own_county_claim_is_valid_without_inventing_a_town(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, item = self.fixture(root)
            saved = json.loads(path.read_text())
            text = item['unitText'].replace('Haddenham, Buckinghamshire', 'Buckinghamshire, UK')
            saved['results'][0]['evidence']['text'] = text
            atomic_json(path, saved)
            item.update(unitText=text, localityClaim='Buckinghamshire',
                        localityQuote='I am based in Buckinghamshire, UK.',
                        checkpointSha256=hashlib.sha256(path.read_bytes()).hexdigest())
            report = self.run_review(root, [item])
            self.assertEqual(report['newResearchContacts'], 1)
            row = json.loads((root / 'review/public-contacts/new.json').read_text())[0]
            self.assertEqual(row['localityClaim'], 'Buckinghamshire')
            self.assertFalse(row['localVerified'])
            self.assertFalse(row['outreachEligible'])

    def test_county_name_cannot_be_used_as_a_town_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            item.update(localityClaim='Buckingham', localityQuote='Buckinghamshire')
            self.assertEqual(self.run_review(root, [item])['held'], 1)

    def test_known_business_host_or_phone_is_not_an_additional_contact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            item.update(sourceKind='own_site', website='https://village.example.org/')
            atomic_json(root / 'baseline/contacts.json', [{'canonicalId': 'business:old:',
                'name': 'Earlier business name', 'sourceUrls': ['http://www.village.example.org/contact']}])
            self.assertEqual(self.run_review(root, [item])['newResearchContacts'], 0)
            atomic_json(root / 'baseline/contacts.json', [{'canonicalId': 'business:old:+447700900123',
                'name': 'Earlier business name', 'sourceUrls': []}])
            item.update(sourceKind='public_advert', website=None)
            self.assertEqual(self.run_review(root, [item])['newResearchContacts'], 0)

    def test_own_site_cannot_use_another_business_or_directory_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            report = self.run_review(root, [dict(item, sourceKind='own_site', website='https://other.example.net/')])
            self.assertEqual(report['held'], 1)

    def test_own_site_can_advertise_a_contact_page_without_email_or_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, item = self.fixture(root)
            saved = json.loads(path.read_text())
            url = 'https://village.example.org/contact/'
            saved['results'][0]['evidence']['links'] = [{'url': url, 'anchor': 'Contact Jane'}]
            atomic_json(path, saved)
            item.update(checkpointSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        sourceKind='own_site', website='https://village.example.org/',
                        email=None, phone=None, contactUrl=url)
            self.assertEqual(self.run_review(root, [item])['newResearchContacts'], 1)
            row = json.loads((root / 'review/public-contacts/new.json').read_text())[0]
            self.assertEqual(row['contactUrl'], url)
            self.assertEqual(row['pageEvidence'][0]['contactLink']['anchor'], 'Contact Jane')
            self.assertIsNone(row['email'])
            self.assertFalse(row['outreachEligible'])
            from new_leads import run
            output = root / 'exports/contact-delta'
            run(root / 'baseline/contacts.json', root / 'review/public-contacts/new.json', output)
            import csv
            with (output / 'new.csv').open() as stream:
                exported = list(csv.DictReader(stream))[0]
            self.assertEqual(exported['contactUrl'], url)

    def test_contact_route_cannot_be_invented_or_taken_from_a_neighbour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, item = self.fixture(root)
            saved = json.loads(path.read_text())
            good = 'https://village.example.org/contact/'
            saved['results'][0]['evidence']['links'] = [
                {'url': good, 'anchor': 'Contact Jane'},
                {'url': 'https://village.example.org/terms/', 'anchor': 'Terms'},
                {'url': 'https://neighbour.example.net/contact/', 'anchor': 'Contact neighbour'}]
            atomic_json(path, saved)
            item.update(checkpointSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        sourceKind='own_site', website='https://village.example.org/', email=None, phone=None)
            for bad in [dict(item, contactUrl='https://village.example.org/unadvertised/'),
                        dict(item, contactUrl='https://village.example.org/terms/'),
                        dict(item, contactUrl='https://neighbour.example.net/contact/'),
                        dict(item, contactUrl=good, sourceKind='public_advert', website=None)]:
                with self.subTest(bad=bad):
                    self.assertEqual(self.run_review(root, [bad])['held'], 1)

    def test_aggregate_rechecks_current_run_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            self.run_review(root, [item])
            atomic_json(root / 'profile-audit/this-stage-delta/new.json', [{'canonicalId': 'profile:jane',
                'name': 'Jane Smith', 'sourceUrls': ['https://tutor.example.net/jane'], 'outreachEligible': False}])
            from campaign_watch import aggregate_contacts
            report = aggregate_contacts(root, {'outputDirectory': str(root / 'profile-audit')})
            self.assertEqual(report['delta']['newAcceptedResearchIdentities'], 1)
            self.assertEqual(len(json.loads((root / 'review/public-contacts/current-run-needs-review.json').read_text())), 1)

    def test_two_adverts_sharing_a_route_are_held_without_arbitrary_identity_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, item = self.fixture(root)
            saved = json.loads(path.read_text())
            second = json.loads(json.dumps(saved['results'][0]))
            second['evidence']['text'] = second['evidence']['text'].replace('Jane Smith', 'Alex Brown')
            saved['results'].append(second)
            atomic_json(path, saved)
            item['checkpointSha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            other = dict(item, resultIndex=1, name='Alex Brown', nameQuote='Alex Brown',
                         unitText=second['evidence']['text'])
            report = self.run_review(root, [item, other])
            self.assertEqual(report['newResearchContacts'], 0)
            self.assertEqual(report['held'], 2)

    def test_explicit_identity_hold_and_malformed_record_cannot_add_contacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            report = self.run_review(root, [None, dict(item, manualHoldReason='Possible existing owner')])
            self.assertEqual(report['newResearchContacts'], 0)
            self.assertEqual(report['held'], 2)

    def test_focused_sqlite_retains_reusable_proof_after_fetch_scratch_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, item = self.fixture(root)
            evidence = json.loads(path.read_text())['results'][0]['evidence']
            evidence['fetchCheckpointSha256'] = item['checkpointSha256']
            encoded = json.dumps(evidence)
            focused = root / 'focused-parent'
            focused.mkdir()
            with closing(sqlite3.connect(focused / 'frontier.sqlite')) as db, db:
                db.execute('CREATE TABLE pages (key TEXT, state TEXT, evidence TEXT)')
                db.execute('INSERT INTO pages VALUES (?,?,?)', ('page-key', 'classified', encoded))
            atomic_json(root / 'manifest.json', {'focusedParent': str(focused)})
            item.update(sourceType='focused_page', pageKey='page-key',
                        evidenceSha256=hashlib.sha256(encoded.encode()).hexdigest())
            path.unlink()
            self.assertEqual(self.run_review(root, [item])['newResearchContacts'], 1)
            with closing(sqlite3.connect(focused / 'frontier.sqlite')) as db, db:
                db.execute('UPDATE pages SET evidence=?', (encoded + ' ',))
            self.assertEqual(self.run_review(root, [item])['held'], 1)

    def test_html_advert_unit_cannot_borrow_the_next_cards_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, item = self.fixture(root)
            page = root / 'public-source-review/village/page.html'
            page.parent.mkdir(parents=True)
            page.write_text('<div id="jane">' + item['unitText'] + '</div><div id="alex">Contact alex@example.net</div>')
            receipt = page.parent / 'receipt.json'
            atomic_json(receipt, {'url': 'https://village.example.org/services', 'status': 200,
                'sha256': hashlib.sha256(page.read_bytes()).hexdigest(), 'observedAt': '2026-10-13'})
            item.update(sourceType='html_advert', page=str(page.relative_to(root)),
                receipt=str(receipt.relative_to(root)), receiptSha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
                unitId='jane')
            self.assertEqual(self.run_review(root, [item])['newResearchContacts'], 1)
            self.assertEqual(self.run_review(root, [dict(item, email='alex@example.net')])['held'], 1)
            self.assertEqual(self.run_review(root, [dict(item, unitId='alex')])['held'], 1)
            page.write_text(page.read_text() + 'changed')
            self.assertEqual(self.run_review(root, [item])['held'], 1)

    def test_malformed_secondary_candidate_artifact_does_not_stop_discovery(self):
        from discovery import PaidCache, collect_records
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = PaidCache(root, 100, 0)
            atomic_json(root / 'review/candidates/bad.json', [{'reviewer': 'codex'}])
            atomic_json(root / 'review/candidates/good.json', {'leads': [{'name': 'Jane Smith',
                'website': 'https://jane.example.org/', 'sourceUrls': ['https://jane.example.org/'],
                'services': ['English tuition'], 'outreachEligible': False}, None]})
            try:
                rows = collect_records(cache, root)
                self.assertEqual([r['name'] for r in rows], ['Jane Smith'])
                errors = json.loads((root / 'review/candidate-import-errors.json').read_text())
                self.assertEqual(len(errors), 2)
                self.assertEqual(cache.usage()['spentCents'], 0)
            finally:
                cache.db.close()


if __name__ == '__main__':
    unittest.main()
