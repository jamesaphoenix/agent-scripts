import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from owned_context_refresh import refresh
from qualify_inventory import candidate_key, create_database

SCOPE = {'subjects': ['english', 'maths'], 'includeElevenPlus': True,
         'includePrimaryTutors': True, 'excludeMusicOnly': True}


class OwnedContextRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = create_database(Path(self.tmp.name) / 'candidates.sqlite'); self.addCleanup(self.db.close)
        bank = sqlite3.connect(':memory:'); bank.row_factory = sqlite3.Row; self.addCleanup(bank.close)
        bank.execute('CREATE TABLE decision_requests(key TEXT,state TEXT,reservation INTEGER)')
        bank.execute("INSERT INTO decision_requests VALUES('settled','succeeded',77)")
        self.cache = SimpleNamespace(db=bank)
        self.url = 'https://jane.example/'
        self.target = self.url + 'tutoring'
        self.candidate = {'website': self.url, 'name': 'Jane Education'}
        self.key = candidate_key(self.candidate)
        self.decision = {'decisionKey': 'settled', 'kind': 'independent_tutor',
                         'kindProbability': .97, 'local': .9, 'outside': 0}
        self.page = {'url': self.url, 'title': 'Jane Education',
            'text': 'English reading support in Milton Keynes.',
            'replaySource': {'checkpoint': '/cached/crawl.json', 'sha256': 'a' * 64},
            'links': [{'url': self.target, 'anchor': 'Tutoring'}]}
        self.index = {self.url: self.page}

    def insert(self, state='subject_review', candidate=None, decision=None, pages=None):
        candidate = self.candidate if candidate is None else candidate
        decision = self.decision if decision is None else decision
        pages = [self.page] if pages is None else pages
        self.db.execute('INSERT INTO candidates(key,candidate,state,evidence,decision) VALUES(?,?,?,?,?)',
            (self.key, json.dumps(candidate), state, json.dumps(pages), json.dumps(decision)))
        self.db.commit()

    def test_new_observed_offer_requeues_without_changing_original_evidence_or_paid_receipt(self):
        self.insert()
        before = self.db.execute('SELECT candidate,evidence,decision FROM candidates').fetchone()
        receipts = self.cache.db.execute('SELECT * FROM decision_requests').fetchall()
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 1)
        self.assertEqual(self.db.execute('SELECT state FROM candidates').fetchone()[0], 'pending')
        self.assertEqual(self.db.execute('SELECT candidate,evidence,decision FROM candidates').fetchone(), before)
        self.assertEqual(self.cache.db.execute('SELECT * FROM decision_requests').fetchall(), receipts)
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)
        richer = dict(self.page, url=self.target, text='I teach English and maths in Milton Keynes.')
        with self.db:
            self.db.execute("UPDATE candidates SET state='tutor_research_candidate',evidence=?",
                            (json.dumps([self.page, richer]),))
        self.index[self.target] = richer
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)

    def test_uncertain_missing_and_held_receipts_are_preserved(self):
        for state in ('uncertain', None):
            with self.subTest(state=state):
                self.db.execute('DELETE FROM candidates')
                self.cache.db.execute('DELETE FROM decision_requests')
                if state:
                    self.cache.db.execute('INSERT INTO decision_requests VALUES(?,?,77)', ('settled', state))
                self.insert()
                self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)
        for state in ('decision_held', 'fetch_held', 'outside_area'):
            self.db.execute('DELETE FROM candidates'); self.insert(state=state)
            self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)

    def test_outside_and_institution_decisions_do_not_gain_a_retry(self):
        for change in ({'local': .5}, {'outside': .8}, {'kind': 'institution'}, {'kindProbability': .5}):
            with self.subTest(change=change):
                self.db.execute('DELETE FROM candidates'); self.insert(decision=dict(self.decision, **change))
                self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)

    def test_new_context_must_stay_with_its_owner_and_have_replay_proof(self):
        self.insert()
        for page in (dict(self.page, replaySource=None), dict(self.page, text='Piano lessons in Milton Keynes.'),
                     dict(self.page, links=[{'url': 'https://other.example/tutoring', 'anchor': 'Tutoring'}])):
            with self.subTest(page=page):
                self.assertEqual(refresh(self.db, self.cache, {self.url: page}, SCOPE, {}), 0)
        self.index[self.target] = dict(self.page, url='https://other.example/tutoring')
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)

    def test_blocked_failed_and_exhausted_fetches_do_not_loop(self):
        self.insert()
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {'blockedUrls': [self.target]}), 0)
        self.db.execute('INSERT INTO fetch_claims VALUES(?,?,?,?,?)', (self.target, 'held', None, 'robots denied', 1))
        self.db.commit()
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)
        self.db.execute('DELETE FROM fetch_claims')
        for n in range(8):
            self.db.execute('INSERT INTO fetch_claims VALUES(?,?,?,?,?)',
                            (self.url + str(n), 'succeeded', None, None, 1))
        self.db.commit()
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)

    def test_cached_redirect_alias_does_not_requeue_identical_final_context(self):
        final = dict(self.page, url=self.target)
        self.insert(pages=[final])
        self.index[self.target] = final
        self.assertEqual(refresh(self.db, self.cache, self.index, SCOPE, {}), 0)

    def test_directory_and_shared_tenant_boundaries_are_preserved(self):
        for url, target in [('https://www.childcare.co.uk/profile/111', 'https://www.childcare.co.uk/profile/222'),
                            ('https://jane.wixsite.com/tuition', 'https://jane.wixsite.com/other/tutoring')]:
            self.db.execute('DELETE FROM candidates')
            page = dict(self.page, url=url, links=[{'url': target, 'anchor': 'Tutoring'}])
            self.insert(candidate=dict(self.candidate, website=url), pages=[page])
            self.assertEqual(refresh(self.db, self.cache, {url: page}, SCOPE, {}), 0)
