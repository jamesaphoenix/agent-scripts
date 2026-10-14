import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent-loops/tutor-lead-pathways'))
from qualify_inventory import input_evidence, owned_questions, outcome, run, loaded_source_sha, cleanup
from decisions_classifier import parse_decision, DecisionsCache as RealCache
from discovery import file_hash
from test_decisions_classifier import response

SCOPE={'subjects':['english','maths','verbal_reasoning','non_verbal_reasoning'],
       'includeElevenPlus':True,'includePrimaryTutors':True,'excludeMusicOnly':True}


class InventoryQualificationTests(unittest.TestCase):
    def test_real_source_revision_helper_supports_the_startup_call(self):
        self.assertRegex(loaded_source_sha(),r'^[a-f0-9]{40}$')

    def test_cleanup_preserves_source_checkpoints_paid_cache_and_diagnostic_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'fetches/one';folder.mkdir(parents=True)
            for name in ['crawl.json','seeds.txt','sources.json']:(folder/name).write_text('saved')
            (folder/'stderr.log').write_text('Useful failure evidence')
            (root/'decisions.sqlite').write_text('budget proof')
            report=cleanup(root)
            self.assertEqual(len(report['removed']),2)
            self.assertTrue((folder/'crawl.json').exists())
            self.assertTrue((folder/'stderr.log').exists())
            self.assertEqual((root/'decisions.sqlite').read_text(),'budget proof')

    def test_prompt_demands_owned_requested_subjects_and_locality(self):
        q=owned_questions(['Amersham'],SCOPE)
        self.assertEqual([x['name'] for x in q],['kind','niche','local','outside','eleven_plus'])
        self.assertIn('Music-only',q[1]['instructions'])
        self.assertIn('neighbouring adverts',q[1]['instructions'])
        self.assertIn('query location cannot',q[2]['instructions'])
        self.assertIn('small owner-run',q[0]['instructions'])

    def test_metadata_and_source_text_stay_distinct_without_inferred_contacts(self):
        raw=input_evidence({'name':'Maths tutor Amersham','website':'https://example.org/',
                            'email':'private@example.org'},[{'url':'https://example.org/','text':'I teach piano.'}])
        value=json.loads(raw['text'])
        self.assertEqual(value['ownedPageObservations'][0]['text'],'I teach piano.')
        self.assertNotIn('email',value['candidate'])

    def test_music_and_metadata_only_do_not_become_tutor_contacts(self):
        decision=parse_decision(response())
        self.assertEqual(outcome(decision,[],SCOPE),'needs_owned_page_review')
        self.assertEqual(outcome(decision,[{'text':'I offer piano tuition in Amersham.'}],SCOPE),'subject_review')
        self.assertEqual(outcome(decision,[{'text':'I offer maths tuition in Amersham.'}],SCOPE),'tutor_research_candidate')
        self.assertEqual(outcome(dict(decision,local=.5),[{'text':'Maths tuition.'}],SCOPE),'locality_review')

    def test_durable_batch_resumes_without_paid_replays_and_survives_a_bad_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            scope=dict(SCOPE,schemaVersion=1,authorization={'instruction':'English and maths tutors','recordedAt':'2026-10-14T08:40:00Z'})
            (root/'discovery-scope.json').write_text(json.dumps(scope))
            (root/'crawler').write_text('test crawler')
            candidates=[{'name':name,'website':f'https://{name.lower()}.example/','status':'research'}
                        for name in ['Ready','Broken','Fetched']]
            (root/'input.json').write_text(json.dumps(candidates))
            legacy=RealCache(root/'legacy.sqlite',['Amersham']);legacy.close()
            job={'input':str(root/'input.json'),'scope':str(root/'discovery-scope.json'),'crawler':str(root/'crawler'),
                 'legacyCache':str(root/'legacy.sqlite'),'frontiers':[],'crawlRoots':[],'towns':['Amersham'],
                 'deadlineUnix':time.time()+30,'authorizedDeadline':'test deadline',
                 'authorization':{'instruction':'Allocate another $10-$20 for classification etc.',
                     'recordedAt':'2026-10-14T08:40:00Z','additionalCapCents':1000}}
            for field in ('input','scope','crawler'):job[field+'Sha256']=file_hash(Path(job[field]))
            (root/'job.json').write_text(json.dumps(job))
            calls=[];fetches=[]
            def transport(payload):
                calls.append(payload)
                return response()
            def cache_factory(*args,**kwargs):return RealCache(*args,transport=transport,**kwargs)
            def page(url):return {'url':url,'text':'I offer maths tutoring in Amersham, with individual lessons for each pupil.'}
            def fetcher(job,root,row):
                fetches.append(row['url'])
                if 'broken' in row['url']:raise ValueError('Failed fetch')
                return page(row['url'])
            index={'https://ready.example/':page('https://ready.example/')}
            with patch('qualify_inventory.loaded_source_sha',return_value='a'*40), \
                 patch('qualify_inventory.DecisionsCache',side_effect=cache_factory), \
                 patch('qualify_inventory.page_index',return_value=index), \
                 patch('qualify_inventory.fetch',side_effect=fetcher):
                run(root)
                first=json.loads((root/'status.json').read_text())
                self.assertEqual(first['phase'],'complete')
                self.assertEqual(first['counts'],{'fetch_held':1,'tutor_research_candidate':2})
                self.assertEqual(len(calls),2)
                self.assertEqual(len(fetches),2)
                index['https://broken.example/']=page('https://broken.example/')
                run(root)
                self.assertEqual(len(calls),3)
                self.assertEqual(len(fetches),2)
                self.assertEqual(json.loads((root/'status.json').read_text())['counts'],{'tutor_research_candidate':3})
                run(root)
                self.assertEqual(len(calls),3)
                self.assertEqual(json.loads((root/'status.json').read_text())['newAuditedContacts'],0)


if __name__=='__main__':unittest.main()
