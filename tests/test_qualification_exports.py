import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from qualification_exports import extract_profile, partition_profiles, settled_decision, publish
from decisions_classifier import DecisionsCache
from qualify_inventory import input_evidence, owned_questions, VERSION, create_database
from discovery import file_hash
from test_decisions_classifier import response

SCOPE = {'subjects': ['english', 'maths'], 'includeElevenPlus': True,
         'includePrimaryTutors': True, 'excludeMusicOnly': True}


class QualificationExportTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'name': 'Jane Smith Tuition', 'website': 'https://jane.example/'}
        self.page = {'url': 'https://jane.example/', 'text':
                     'Jane Smith Tuition. I offer maths tuition in Amersham. Contact jane@jane.example.',
                     'replaySource': {'checkpoint': '/saved/crawl.json', 'sha256': 'a' * 64}}
        self.index = {self.page['url']: self.page}

    def test_missing_email_does_not_block_a_qualified_research_profile(self):
        page = dict(self.page, text='Jane Smith Tuition. I offer maths tuition in Amersham.')
        row = extract_profile('key', self.candidate, [page], {page['url']: page}, SCOPE, ['Amersham'])
        self.assertEqual(row['emailStatus'], 'missing')
        self.assertEqual(row['emails'], [])
        self.assertEqual(row['matchedSubjects'], ['maths'])
        self.assertFalse(row['outreachEligible'])

    def test_contacts_are_observed_not_taken_from_search_metadata(self):
        candidate = dict(self.candidate, email='invented@example.net')
        row = extract_profile('key', candidate, [self.page], self.index, SCOPE, ['Amersham'])
        self.assertEqual(row['emails'][0]['email'], 'jane@jane.example')
        self.assertEqual(row['emails'][0]['sourceUrl'], self.page['url'])
        self.assertFalse(row['emails'][0]['verified'])
        self.assertNotIn('invented', json.dumps(row))

    def test_changed_context_and_foreign_owner_do_not_pass(self):
        with self.assertRaisesRegex(ValueError, 'replay'):
            extract_profile('key', self.candidate, [dict(self.page, text='changed')], self.index, SCOPE, ['Amersham'])
        other = dict(self.page, url='https://another.example/')
        with self.assertRaisesRegex(ValueError, 'owner'):
            extract_profile('key', self.candidate, [other], {other['url']: other}, SCOPE, ['Amersham'])

    def test_ambiguous_name_or_locality_stays_for_review(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            extract_profile('key', dict(self.candidate, name='Jane'), [self.page], self.index, SCOPE, ['Amersham'])
        page = dict(self.page, text='Jane Smith Tuition. I offer maths tuition online nationwide.')
        with self.assertRaisesRegex(ValueError, 'locality'):
            extract_profile('key', self.candidate, [page], {page['url']: page}, SCOPE, ['Amersham'])

    def test_unparsed_business_listings_cannot_supply_owned_website_identity(self):
        for url in ('https://www.infobel.com/en/uk/jane_smith/amersham/businessdetails.aspx',
                    'https://www.netmums.com/local/l/jane-smith-tuition'):
            with self.subTest(url=url):
                page = dict(self.page, url=url)
                with self.assertRaisesRegex(ValueError, 'Directory text'):
                    extract_profile('key', dict(self.candidate, website=url), [page], {url: page}, SCOPE, ['Amersham'])

    def test_an_article_headline_cannot_be_published_as_a_tutor_name(self):
        headline = 'How to Choose the Best Maths Tutor in Amersham'
        page = dict(self.page, title=headline, text=headline + '. ' + self.page['text'])
        with self.assertRaisesRegex(ValueError, 'identity'):
            extract_profile('key', dict(self.candidate, name=headline), [page], {page['url']: page}, SCOPE, ['Amersham'])

    def test_search_title_decoration_and_legal_suffix_need_owned_title_corroboration(self):
        page = dict(self.page, title='Home | Jane Smith Tuition')
        for label in ['Registration | Jane Smith Tuition', 'Jane Smith Tuition Ltd', 'Jane Smith Tuition Amersham']:
            with self.subTest(label=label):
                row = extract_profile('key', dict(self.candidate, name=label), [page], {page['url']: page}, SCOPE, ['Amersham'])
                self.assertEqual(row['name'], 'Jane Smith Tuition')
        with self.assertRaisesRegex(ValueError, 'identity'):
            extract_profile('key', dict(self.candidate, name='John Brown Tuition Ltd'), [page], {page['url']: page}, SCOPE, ['Amersham'])

    def test_title_fallback_cannot_choose_between_two_named_owners(self):
        page = dict(self.page, title='Jane Smith Tuition | John Brown Tuition',
                    text=self.page['text'] + ' John Brown Tuition.')
        with self.assertRaisesRegex(ValueError, 'identity'):
            extract_profile('key', dict(self.candidate, name='Jane Smith Tuition | John Brown Tuition'), [page], {page['url']: page}, SCOPE, ['Amersham'])

    def test_a_teachers_full_self_introduction_requires_an_own_about_me_page(self):
        candidate = dict(self.candidate, name='JS Education - Dyslexia Assessment and Tuition')
        page = dict(self.page, title='Specialist Teacher - About me',
                    text='About Me. My name is Jo Souter and I am an experienced specialist teacher. '
                         'I offer reading and spelling tuition in Amersham.')
        profile = extract_profile('key', candidate, [page], {page['url']: page}, SCOPE, ['Amersham'])
        self.assertEqual(profile['name'], 'Jo Souter')
        self.assertFalse(profile['outreachEligible'])
        for title, text in [('Testimonials', page['text']),
                            ('About me', page['text'].replace('specialist teacher', 'happy parent')),
                            ('About me', 'About Me. I run JS Education. Testimonials. '
                             'My name is Jane Parent and I am a primary teacher. Tuition in Amersham.'),
                            ('About me', page['text'].replace('Jo Souter', 'Jo'))]:
            with self.subTest(title=title, text=text):
                changed = dict(page, title=title, text=text)
                with self.assertRaisesRegex(ValueError, 'identity'):
                    extract_profile('key', candidate, [changed], {changed['url']: changed}, SCOPE, ['Amersham'])

    def test_conflicting_self_introductions_stay_held(self):
        page = dict(self.page, title='About me',
                    text='About Me. My name is Jane Smith and I am a maths teacher. '
                         'About Me. My name is John Brown and I am an English tutor. Tuition in Amersham.')
        with self.assertRaisesRegex(ValueError, 'identity'):
            extract_profile('key', dict(self.candidate, name='Ambiguous practice'), [page],
                            {page['url']: page}, SCOPE, ['Amersham'])

    def test_identity_match_requires_whole_words_and_retains_full_person_names(self):
        page = dict(self.page, title='Joann Smith Tuition', text='Joann Smith Tuition. I offer maths tuition in Amersham.')
        with self.assertRaisesRegex(ValueError, 'identity'):
            extract_profile('key', dict(self.candidate, name='Ann Smith Tuition'), [page], {page['url']: page}, SCOPE, ['Amersham'])
        page = dict(self.page, title='Jane Smith', text='Jane Smith. I offer maths tuition in Amersham.')
        with self.assertRaisesRegex(ValueError, 'identity'):
            extract_profile('key', dict(self.candidate, name='Jane Smith Amersham'), [page], {page['url']: page}, SCOPE, ['Amersham'])

    def test_duplicate_sites_and_existing_exports_are_not_added_again(self):
        row = extract_profile('one', self.candidate, [self.page], self.index, SCOPE, ['Amersham'])
        other = dict(row, candidateId='two', canonicalId='different')
        split = partition_profiles([], [row, other])
        self.assertEqual(len(split['accepted']), 1)
        self.assertEqual(len(split['known']), 1)
        split = partition_profiles([row], [other])
        self.assertEqual(split['accepted'], [])
        self.assertEqual(len(split['known']), 1)

    def test_public_profile_advertised_site_deduplicates_its_business_record(self):
        url = 'https://www.childcare.co.uk/profile/111'
        fields = {'name': 'Jane Smith', 'platform': 'childcare.co.uk', 'personId': '111',
                  'localityClaim': 'Amersham', 'services': ['Maths'],
                  'website': 'https://jane.example/', 'organization': 'Jane Smith Tuition'}
        page = {'url': url, 'text': json.dumps({'publicProfileFields': fields}),
                'extractionKind': 'public_profile_fields_v1',
                'publicContacts': {'emails': ['jane@jane.example'], 'phone': '07722 868342'},
                'replaySource': {'receipt': '/saved/receipt.json', 'receiptSha256': 'a' * 64}}
        profile = extract_profile('profile', {'website': url}, [page], {url: page}, SCOPE, ['Amersham'])
        business = extract_profile('business', self.candidate, [self.page], self.index, SCOPE, ['Amersham'])
        self.assertEqual(profile['website'], 'https://jane.example/')
        self.assertEqual(profile['phone'], '07722 868342')
        self.assertEqual(profile['emailStatus'], 'observed')
        self.assertEqual(len(partition_profiles([], [profile, business])['accepted']), 1)

    def test_public_profile_county_conflict_cannot_be_overridden_by_a_local_city(self):
        url = 'https://thetutorsassociation.wildapricot.org/Sys/PublicProfile/111/6119301'
        fields = {'name': 'Jane Smith', 'platform': 'thetutorsassociation.wildapricot.org',
                  'personId': '111', 'localityClaim': 'Amersham', 'profileCity': 'Amersham',
                  'profileCounty': 'Essex', 'services': ['Maths']}
        page = {'url': url, 'text': json.dumps({'publicProfileFields': fields}),
                'extractionKind': 'public_profile_fields_v1',
                'replaySource': {'receipt': '/saved/receipt.json', 'receiptSha256': 'a' * 64}}
        with self.assertRaisesRegex(ValueError, 'locality'):
            extract_profile('profile', {'website': url}, [page], {url: page}, SCOPE, ['Amersham'])

    def test_business_following_its_named_tutor_organisation_is_held(self):
        business = extract_profile('business', self.candidate, [self.page], self.index, SCOPE, ['Amersham'])
        profile = {'canonicalId': 'tutor:111', 'memberKeys': ['childcare.co.uk:111'], 'name': 'Jane Smith',
                   'organization': 'Jane Smith Tuition', 'sourceUrls': ['https://www.childcare.co.uk/profile/111'],
                   'website': None, 'outreachEligible': False}
        result = partition_profiles([profile], [business])
        self.assertEqual(result['accepted'], [])
        self.assertEqual(result['held'][0]['deltaHoldReason'], 'possible_baseline_organisation_match')
    def test_name_collisions_are_held_without_merging_distinct_people(self):
        row = extract_profile('one', self.candidate, [self.page], self.index, SCOPE, ['Amersham'])
        other = dict(row, canonicalId='different', memberKeys=['website:other.example'], website='https://other.example/',
                     sourceUrls=['https://other.example/'], emails=[], email=None)
        split = partition_profiles([row], [other])
        self.assertEqual(split['accepted'], [])
        self.assertEqual(len(split['held']), 1)

    def test_paid_receipt_replay_is_read_only_and_bound_to_exact_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'decisions.sqlite'
            auth = {'instruction': 'Allocate $10', 'recordedAt': '2026-10-14T12:00:00Z', 'additionalCapCents': 1000}
            qs = owned_questions(['Amersham'], SCOPE)
            cache = DecisionsCache(path, ['Amersham'], transport=lambda _: response(),
                cap_nano=10_000_000_000, prior_exposure_cents=10000,
                additional_authorization=auth, question_set=qs, prompt_version=VERSION)
            decision = cache.classify(input_evidence(self.candidate, [self.page], SCOPE))
            before = cache.summary()
            result = settled_decision(cache.db, decision, self.candidate, [self.page], qs, SCOPE)
            self.assertEqual(result['kind'], 'independent_tutor')
            self.assertEqual(cache.summary(), before)
            for policy, revision in ((SCOPE, 1), (None, 1)):
                old = cache.classify(input_evidence(self.candidate, [self.page], policy, revision))
                old_usage = cache.summary()
                self.assertEqual(settled_decision(cache.db, old, self.candidate, [self.page], qs, SCOPE)['kind'], 'independent_tutor')
                self.assertEqual(cache.summary(), old_usage)
            with self.assertRaisesRegex(ValueError, 'contract'):
                settled_decision(cache.db, decision, self.candidate, [dict(self.page, text='different')], qs, SCOPE)
            with cache.db:
                cache.db.execute("UPDATE decision_requests SET state='uncertain'")
            with self.assertRaisesRegex(ValueError, 'settled'):
                settled_decision(cache.db, decision, self.candidate, [self.page], qs, SCOPE)
            cache.close()

    def test_export_restarts_reuse_receipts_and_preserve_audited_contacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'exports').mkdir()
            baseline = root / 'baseline.json'; baseline.write_text('[]')
            audited = root / 'exports/audited-subject-contacts.json'; audited.write_text('[]')
            page = dict(self.page, text='Jane Smith Tuition offers maths tuition in Amersham.')
            auth = {'instruction': 'Allocate $10', 'recordedAt': '2026-10-14T12:00:00Z', 'additionalCapCents': 1000}
            qs = owned_questions(['Amersham'], SCOPE)
            calls = []
            def transport(payload): calls.append(payload); return response()
            cache = DecisionsCache(root / 'decisions.sqlite', ['Amersham'], transport=transport,
                cap_nano=10_000_000_000, prior_exposure_cents=10000,
                additional_authorization=auth, question_set=qs, prompt_version=VERSION)
            decision = cache.classify(input_evidence(self.candidate, [page], SCOPE))
            db = create_database(root / 'candidates.sqlite')
            with db:
                db.execute('INSERT INTO candidates(key,candidate,state,evidence,decision) VALUES(?,?,?,?,?)',
                           ('one', json.dumps(self.candidate), 'tutor_research_candidate', json.dumps([page]), json.dumps(decision)))
            job = {'knownContacts': {'path': str(baseline), 'sha256': file_hash(baseline)},
                   'towns': ['Amersham'], 'loadedSourceSha': 'a' * 40}
            before = cache.summary(), audited.read_bytes()
            for _ in range(2):
                summary = publish(root, job, db, SCOPE, {page['url']: page}, qs)
                self.assertEqual(summary['acceptedResearchProfiles'], 1)
                self.assertEqual(summary['missingEmailProfiles'], 1)
            self.assertEqual(before, (cache.summary(), audited.read_bytes()))
            self.assertEqual(len(calls), 1)
            db.close(); cache.close()


if __name__ == '__main__': unittest.main()
