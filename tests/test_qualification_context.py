import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from qualification_context import context_urls, select_urls, select_pages, fallback_pages, profile_context_index, cache_path


class QualificationContextTests(unittest.TestCase):
    def test_an_observed_tuition_page_gets_a_slot_beside_home_and_contact(self):
        url = 'https://jane.example/'
        index = {url: {'url': url, 'links': [
            {'url': url + 'about', 'anchor': 'About'},
            {'url': url + 'contact', 'anchor': 'Contact'},
            {'url': url + 'tutoring', 'anchor': 'Tutoring'},
            {'url': 'https://another.example/tuition', 'anchor': 'Tuition'}]}}
        self.assertEqual(select_urls({'website': url}, index), [url, url + 'contact', url + 'tutoring'])

    def test_existing_selection_order_stays_stable_without_a_new_offering_page(self):
        url = 'https://jane.example/'
        index = {url: {'url': url, 'links': [
            {'url': url + 'about', 'anchor': 'About'},
            {'url': url + 'contact', 'anchor': 'Contact'},
            {'url': url + 'blog/how-to-choose-maths-tuition', 'anchor': 'Maths tuition guide'},
            {'url': url + 'login/tuition', 'anchor': 'Tuition login'}]}}
        self.assertEqual(select_urls({'website': url}, index), [url, url + 'about', url + 'contact'])

    def test_one_offering_slot_does_not_displace_an_already_selected_subject_page(self):
        url = 'https://jane.example/'
        candidate = {'website': url, 'sourceUrl': url + 'maths-tuition'}
        index = {url: {'url': url, 'links': [
            {'url': url + 'about', 'anchor': 'About'},
            {'url': url + 'contact', 'anchor': 'Contact'},
            {'url': url + 'english-tuition', 'anchor': 'English tuition'}]}}
        self.assertEqual(select_urls(candidate, index), [url, url + 'maths-tuition', url + 'about'])

    def test_plural_navigation_labels_can_nominate_owned_offering_pages(self):
        url = 'https://jane.example/'
        for label in ('Tutoring', 'Services', 'Lessons', 'Courses', 'Literacy', 'Phonics'):
            with self.subTest(label=label):
                target = url + label.casefold()
                index = {url: {'links': [{'url': target, 'anchor': label}]}}
                self.assertIn(target, context_urls({'website': url}, index))

    def test_a_specific_tuition_page_displaces_a_generic_services_index(self):
        url = 'https://jane.example/'
        index = {url: {'url': url, 'links': [
            {'url': url + 'about', 'anchor': 'About'},
            {'url': url + 'services', 'anchor': 'Services'},
            {'url': url + 'tutoring', 'anchor': 'Tutoring'}]}}
        self.assertEqual(select_urls({'website': url}, index), [url, url + 'about', url + 'tutoring'])

    def test_articles_and_archives_cannot_take_the_own_offer_slot(self):
        url = 'https://jane.example/'
        for path in ('advice/why-maths-is-hard', 'tag/online-tutoring',
                     'category/maths', 'tutoring/reviews', 'guides/11-plus-tuition'):
            with self.subTest(path=path):
                index = {url: {'url': url, 'links': [
                    {'url': url + 'about', 'anchor': 'About'},
                    {'url': url + 'contact', 'anchor': 'Contact'},
                    {'url': url + path, 'anchor': 'Maths tuition advice'}]}}
                self.assertEqual(select_urls({'website': url}, index),
                                 [url, url + 'about', url + 'contact'])

    def test_an_article_slug_cannot_hide_the_actual_tuition_page(self):
        url = 'https://jane.example/'
        article = url + 'more-information-about-dyslexia-assessment-and-tuition/'
        tuition = url + 'dyslexia-tuition/'
        about = url + 'specialist-teacher-and-assessor/'
        candidate = {'website': url, 'siteEvidence': [
            {'url': article}, {'url': about}, {'url': tuition}]}
        index = {url: {'url': url, 'links': [
            {'url': article, 'anchor': 'Blog'},
            {'url': about, 'anchor': 'About'},
            {'url': tuition, 'anchor': 'Tuition'}]}}
        self.assertEqual(select_urls(candidate, index), [url, about, tuition])

    def test_fallback_cannot_reuse_changed_text_or_another_owners_page(self):
        url = 'https://jane.example/'
        page = {'url': url, 'title': 'Jane', 'text': 'Current offer', 'replaySource': {'sha256': 'a' * 64}}
        foreign = dict(page, url='https://other.example/')
        index = {url: page, foreign['url']: foreign}
        for prior in ([dict(page, text='Superseded offer')], [foreign]):
            with self.subTest(prior=prior):
                self.assertEqual(fallback_pages({'website': url}, prior, index), [page])

    def test_enrichment_uses_observed_owned_links_and_keeps_profile_and_tenant_boundaries(self):
        candidate={'website':'https://jane.example/'}
        index={'https://jane.example/':{'url':'https://jane.example/','links':[
            {'url':'https://jane.example/contact','anchor':'Contact'},
            {'url':'https://other.example/about','anchor':'About our tutor'},
            {'url':'https://jane.example/login','anchor':'Tutor login'}]}}
        self.assertEqual(context_urls(candidate,index),['https://jane.example/','https://jane.example/contact'])
        candidate={'website':'https://www.childcare.co.uk/profile/111'}
        index={candidate['website']:{'links':[{'url':'https://www.childcare.co.uk/profile/222','anchor':'Tutor'}]}}
        self.assertEqual(context_urls(candidate,index),[candidate['website']])
        candidate={'website':'https://jane.wixsite.com/tuition'}
        index={candidate['website']:{'links':[{'url':'https://jane.wixsite.com/other/contact','anchor':'Contact'}]}}
        self.assertEqual(context_urls(candidate,index),[candidate['website']])

    def test_selection_prefers_direct_owned_context_and_deduplicates_redirect_aliases(self):
        candidate={'website':'http://jane.example/','siteEvidence':[{'url':'https://jane.example/about'}]}
        home={'url':'https://jane.example/','text':'Maths tuition','links':[
            {'url':'https://jane.example/contact','anchor':'Contact'},
            {'url':'https://jane.example/about','anchor':'About'},
            {'url':'https://jane.example/privacy','anchor':'Privacy'}]}
        index={'http://jane.example/':home,'https://jane.example/':home,
               'https://jane.example/about':{'url':'https://jane.example/about'},
               'https://jane.example/contact':{'url':'https://jane.example/contact'}}
        self.assertEqual([p['url'] for p in select_pages(candidate,index)],
                         ['https://jane.example/','https://jane.example/about','https://jane.example/contact'])

    def test_public_profile_context_requires_matching_body_receipt_and_person_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'receipts').mkdir();(root/'pages').mkdir()
            url='https://www.childcare.co.uk/profile/111';key=hashlib.sha256(url.encode()).hexdigest()
            body=b'public profile HTML'
            (root/'pages'/f'{key}.html').write_bytes(body)
            receipt={'url':url,'status':200,'sha256':hashlib.sha256(body).hexdigest(),'contentType':'text/html'}
            path=root/'receipts'/f'{key}.json';path.write_text(json.dumps(receipt))
            profile={'name':'Jane Smith','personId':'111','platform':'childcare.co.uk','sourceUrls':[url],
                     'localityClaim':'Amersham','services':['Maths'],'outreachEligible':False,
                     'email':'public@example.org','phone':'07722 868342'}
            with patch('qualification_context.profile_record',return_value=profile):
                index=profile_context_index([root],[{'website':url}])
                self.assertIn(url,index)
                self.assertEqual(index[url]['sourceDocumentSha256'],receipt['sha256'])
                self.assertIn('Jane Smith',index[url]['text'])
                self.assertNotIn('public@example.org', index[url]['text'])
                self.assertEqual(index[url]['publicContacts']['emails'], ['public@example.org'])
                profile['personId']='222'
                self.assertEqual(profile_context_index([root],[{'website':url}]),{})
                profile['personId']='111';(root/'pages'/f'{key}.html').write_bytes(b'changed')
                self.assertEqual(profile_context_index([root],[{'website':url}]),{})

    def test_shared_classification_cache_is_existing_and_configuration_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cache=root/'shared.sqlite'
            with sqlite3.connect(cache) as db:
                db.execute('CREATE TABLE decision_config(id INTEGER PRIMARY KEY,value TEXT)')
                db.execute('INSERT INTO decision_config VALUES(1,?)',('immutable configuration',))
            proof=hashlib.sha256(b'immutable configuration').hexdigest()
            job={'sharedCache':{'path':str(cache),'configurationSha256':proof}}
            self.assertEqual(cache_path(root/'other-stage',job),cache)
            job['sharedCache']['configurationSha256']='wrong'
            with self.assertRaisesRegex(ValueError,'configuration'):cache_path(root,job)
            job['sharedCache']['path']=str(root/'missing.sqlite')
            with self.assertRaises(ValueError):cache_path(root,job)
            self.assertFalse((root/'missing.sqlite').exists())

    def test_tutorextra_uses_its_numeric_route_and_own_canonical_page(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'pages').mkdir(); (root/'receipts').mkdir()
            url = 'https://tutorextra.co.uk/profile/jane-tutor/50-64971'
            body = f'''<meta property="og:url" content="{url}"><nav>Maths in London</nav>
                <div class="name-type"><div class="fullname">Jane Example - Tutor in High Wycombe</div>
                <div class="usertype">Tutor, Teacher</div></div>
                <table class="responsive-table"><tr><th>Subject</th></tr><tr><td>English lessons</td></tr></table>'''.encode()
            key = hashlib.sha256(url.encode()).hexdigest()
            def save(raw):
                (root/'pages'/f'{key}.html').write_bytes(raw)
                (root/'receipts'/f'{key}.json').write_text(json.dumps({'url':url,'status':200,'sha256':hashlib.sha256(raw).hexdigest()}))
            save(body)
            page = profile_context_index([root],[{'website':url}])[url]
            fields = json.loads(page['text'])['publicProfileFields']
            self.assertEqual(fields['name'], 'Jane Example')
            self.assertEqual(fields['personId'], '64971')
            self.assertEqual(fields['services'], ['English lessons'])
            save(body.replace(url.encode(), url.replace('64971','999').encode()))
            self.assertEqual(profile_context_index([root],[{'website':url}]), {})

    def test_more_owned_profile_formats_replay_with_their_source_identifiers(self):
        from test_teachtutti import profile, URL as teach_url
        from test_apprentus import advert, URL as lesson_url
        tuition_url = 'https://www.tuitionlist.co.uk/tutor/jane-smith-maths-amersham-1234abcd'
        perch_url = 'https://tutorperch.com/t/jane-smith-1234'
        cases = [
            (teach_url, profile(), hashlib.sha256(teach_url.encode()).hexdigest()),
            (lesson_url, advert(), 'rebecca.jade'),
            (perch_url, '<script type="application/ld+json">' + json.dumps({'@type':'ProfilePage',
                'mainEntity':{'@type':'Person','name':'Jane Smith','jobTitle':'Tutor',
                              'areaServed':{'name':'Amersham'},'knowsAbout':['English']}}) + '</script>', 'jane-smith-1234'),
            (tuition_url, '<h1>Jane Smith</h1><section><h2>Subjects and levels</h2><span>Maths</span></section>'
                '<section><h2>Lesson location</h2><p>Jane Smith is listed in Amersham.</p></section>', '1234abcd')]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'pages').mkdir(); (root/'receipts').mkdir()
            for url, content, expected in cases:
                with self.subTest(url=url):
                    key=hashlib.sha256(url.encode()).hexdigest()
                    raw=(f'<meta property="og:url" content="{url}">'+content).encode()
                    (root/'pages'/f'{key}.html').write_bytes(raw)
                    receipt=root/'receipts'/f'{key}.json'
                    receipt.write_text(json.dumps({'url':url,'status':200,'sha256':hashlib.sha256(raw).hexdigest()}))
                    page=profile_context_index([root],[{'website':url}])[url]
                    self.assertEqual(json.loads(page['text'])['publicProfileFields']['personId'],expected)
                    self.assertEqual(page['extractionKind'],'public_profile_fields_v2')
                    self.assertEqual(page['publicContacts']['emails'],[])
                    # Matching body hashes cannot excuse another owner's canonical URL.
                    changed=raw.replace(url.encode(),b'https://another-owner.example/')
                    (root/'pages'/f'{key}.html').write_bytes(changed)
                    receipt.write_text(json.dumps({'url':url,'status':200,'sha256':hashlib.sha256(changed).hexdigest()}))
                    self.assertEqual(profile_context_index([root],[{'website':url}]),{})


if __name__=='__main__':unittest.main()
