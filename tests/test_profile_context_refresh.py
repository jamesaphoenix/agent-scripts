import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from qualify_inventory import create_database, candidate_key, refresh_verified_profile_contexts

SCOPE = {'subjects': ['english', 'maths'], 'includeElevenPlus': True,
         'includePrimaryTutors': True, 'excludeMusicOnly': True}


class ProfileContextRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = create_database(Path(self.tmp.name) / 'candidates.sqlite'); self.addCleanup(self.db.close)
        bank = sqlite3.connect(':memory:'); bank.row_factory = sqlite3.Row; self.addCleanup(bank.close)
        bank.execute('CREATE TABLE decision_requests(key TEXT,state TEXT,reservation INTEGER)')
        self.cache = SimpleNamespace(db=bank)

    def candidate(self, number, state='subject_review', receipt_state='succeeded'):
        url = f'https://tutorextra.co.uk/profile/jane-{number}/50-{number}'
        c = {'website': url, 'name': 'Read more'}; key = candidate_key(c)
        self.db.execute('INSERT INTO candidates(key,candidate,state,evidence,decision) VALUES(?,?,?,?,?)',
            (key,json.dumps(c),state,json.dumps([{'url':url,'text':'Global navigation and truncated page.'}]),json.dumps({'decisionKey':str(number)})))
        self.db.commit()
        self.cache.db.execute('INSERT INTO decision_requests VALUES(?,?,?)',(str(number),receipt_state,77));self.cache.db.commit()
        fields = {'name':'Jane Example','personId':str(number),'platform':'tutorextra.co.uk',
            'localityClaim':'High Wycombe','services':['English lessons'],'institution':False}
        page = {'url':url,'text':json.dumps({'publicProfileFields':fields}),
            'extractionKind':'public_profile_fields_v2','replaySource':{'receipt':'/cached/receipt.json','receiptSha256':'a'*64}}
        return key,url,page,fields

    def test_changed_owned_context_requeues_once_without_resetting_paid_or_held_requests(self):
        key,url,page,_ = self.candidate(1)
        _,held_url,held_page,_ = self.candidate(2, 'decision_held')
        _,uncertain_url,uncertain_page,_ = self.candidate(3, receipt_state='uncertain')
        before = self.cache.db.execute('SELECT * FROM decision_requests').fetchall()
        index = {url:page,held_url:held_page,uncertain_url:uncertain_page}
        self.assertEqual(refresh_verified_profile_contexts(self.db,self.cache,index,SCOPE),1)
        self.assertEqual(self.db.execute('SELECT state FROM candidates WHERE key=?',(key,)).fetchone()[0],'pending')
        self.assertEqual(refresh_verified_profile_contexts(self.db,self.cache,index,SCOPE),0)
        self.assertEqual(self.cache.db.execute('SELECT * FROM decision_requests').fetchall(),before)

    def test_music_outside_institution_conflicts_and_unverified_context_are_not_requeued(self):
        for number,changes in enumerate(({'services':['Piano']},{'localityClaim':'London'},
                {'institution':True},{'locationConflict':True}),1):
            _,url,page,fields = self.candidate(number); fields.update(changes)
            page['text'] = json.dumps({'publicProfileFields':fields})
            self.assertEqual(refresh_verified_profile_contexts(self.db,self.cache,{url:page},SCOPE),0)
        _,url,page,_ = self.candidate(6);page.pop('replaySource')
        self.assertEqual(refresh_verified_profile_contexts(self.db,self.cache,{url:page},SCOPE),0)

    def test_identical_owned_context_does_not_reclassify_a_settled_candidate(self):
        key,url,page,_ = self.candidate(1)
        with self.db:self.db.execute('UPDATE candidates SET evidence=? WHERE key=?',(json.dumps([page]),key))
        self.assertEqual(refresh_verified_profile_contexts(self.db,self.cache,{url:page},SCOPE),0)
