import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from qualification_context import context_urls, select_pages, profile_context_index, cache_path


class QualificationContextTests(unittest.TestCase):
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
                     'localityClaim':'Amersham','services':['Maths'],'outreachEligible':False}
            with patch('qualification_context.profile_record',return_value=profile):
                index=profile_context_index([root],[{'website':url}])
                self.assertIn(url,index)
                self.assertEqual(index[url]['sourceDocumentSha256'],receipt['sha256'])
                self.assertIn('Jane Smith',index[url]['text'])
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


if __name__=='__main__':unittest.main()
