import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent-loops/tutor-lead-pathways'))
from prepare_qualification_followon import prepare
from qualify_inventory import create_database


class QualificationFollowonTests(unittest.TestCase):
    def test_studio_free_window_preserves_states_and_reviewed_supplement(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); parent=base/'previous'; parent.mkdir(); child=base/'next'
            with sqlite3.connect(parent/'decisions.sqlite') as db:
                db.execute('CREATE TABLE decision_config(id INTEGER PRIMARY KEY,value TEXT)')
                db.execute('INSERT INTO decision_config VALUES(1,?)',('sealed configuration',))
            (parent/'discovery-scope.json').write_text('{}')
            inventory=base/'inventory.json'; inventory.write_text('[{"status":"research"}]')
            (parent/'job.json').write_text(json.dumps({'scope':str(parent/'discovery-scope.json'),
                'input':str(inventory),'towns':['Amersham']}))
            (parent/'status.json').write_text('{"phase":"complete","decisions":{}}')
            db=create_database(parent/'candidates.sqlite')
            with db:
                db.execute("INSERT INTO candidates(key,candidate,state,error) VALUES('hold','{}','fetch_held','403')")
                db.execute("INSERT INTO candidates(key,candidate,state) VALUES('negative','{}','excluded_irrelevant')")
            db.close()
            (parent/'exports').mkdir()
            (parent/'exports/audited-subject-contacts.json').write_text('[{"name":"Ros"}]')
            start=datetime.datetime.fromisoformat('2026-10-17T09:03:13+00:00'); auth=base/'authorization.json'
            auth.write_text(json.dumps({'instruction':'Could you continue this work on my Mac Studio? Please using herdr?',
                'requestedAt':start.isoformat(),'deadline':(start+datetime.timedelta(hours=12)).isoformat(),
                'newMoneyAuthorizedCents':0,'maximumRemainingSpendNano':3803813200,
                'paidDataForSeoSearchesAllowed':False,'maximumSdkTurns':0,
                'anthropicApiKeyAllowed':False,'outreachAllowed':False,
                'scopeSha256':'626d1ea33c5d87366917a34f22f3fc8deed5fde3b6227503afa2ffc79d10f5c6'}))
            prepare(parent,child,inventory,auth,base/'profiles')
            with sqlite3.connect(child/'candidates.sqlite') as copied:
                self.assertEqual(copied.execute("SELECT state,error FROM candidates WHERE key='hold'").fetchone(),('fetch_held','403'))
                self.assertEqual(copied.execute("SELECT state FROM candidates WHERE key='negative'").fetchone()[0],'excluded_irrelevant')
            self.assertEqual(json.loads((child/'exports/audited-subject-contacts.json').read_text()),[{'name':'Ros'}])

    def test_new_window_preserves_parent_ledger_fetch_holds_and_uncertain_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);parent=base/'previous';parent.mkdir();child=base/'new-pass'
            ledger=parent/'decisions.sqlite'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE decision_config(id INTEGER PRIMARY KEY,value TEXT)')
                db.execute('INSERT INTO decision_config VALUES(1,?)',('sealed configuration',))
            scope=parent/'discovery-scope.json';scope.write_text('{}')
            inventory=base/'inventory.json';inventory.write_text(json.dumps([{'name':'Jane','status':'research'}]))
            parent_job={'scope':str(scope),'input':str(inventory),'towns':['Amersham'],
                        'authorization':{'additionalCapCents':1000},'deadlineUnix':1,'authorizedDeadline':'old deadline'}
            (parent/'job.json').write_text(json.dumps(parent_job))
            (parent/'status.json').write_text(json.dumps({'phase':'complete','decisions':{'remainingUsd':9.36}}))
            db=create_database(parent/'candidates.sqlite')
            with db:
                for key,state in [('missing','fetch_held'),('review','subject_review'),('uncertain-paid','decision_held')]:
                    db.execute('INSERT INTO candidates(key,candidate,state) VALUES(?,?,?)',(key,'{}',state))
                db.execute("INSERT INTO fetch_claims VALUES('https://example.org/','held',NULL,'403',123)")
            db.close()
            start=datetime.datetime.now(datetime.timezone.utc);auth=base/'approval.json'
            auth.write_text(json.dumps({'instruction':'Yes, start the new pass','requestedAt':start.isoformat(),
                                        'deadline':(start+datetime.timedelta(hours=12)).isoformat()}))
            before=(parent/'job.json').read_bytes(),ledger.read_bytes()
            job=prepare(parent,child,inventory,auth,base/'profiles')
            self.assertEqual(job['sharedCache']['path'],str(ledger.resolve()))
            self.assertFalse((child/'decisions.sqlite').exists())
            self.assertEqual(before,((parent/'job.json').read_bytes(),ledger.read_bytes()))
            with sqlite3.connect(child/'candidates.sqlite') as db:
                states=dict(db.execute('SELECT key,state FROM candidates'))
                self.assertEqual(states,{'missing':'pending','review':'pending','uncertain-paid':'decision_held'})
                self.assertEqual(db.execute('SELECT state,error,started_at FROM fetch_claims').fetchone(),('held','403',123))
            with self.assertRaises(ValueError):prepare(parent,child,inventory,auth,base/'profiles')
            instruction = 'My suggestion is have another $50 of budget and go find more tutors for the next 12 hours plz.'
            auth.write_text(json.dumps({'instruction':instruction,'requestedAt':start.isoformat(),
                'deadline':(start+datetime.timedelta(hours=12)).isoformat(),
                'additionalDiscoveryCents':5000,'previousDiscoveryCapCents':10000,'combinedDiscoveryCapCents':15000}))
            new_job=prepare(parent,base/'additional-discovery-pass',inventory,auth,base/'new-profiles')
            self.assertEqual(new_job['sharedCache'],job['sharedCache'])
            self.assertEqual(new_job['authorizedDeadline'],job['authorizedDeadline'])
            self.assertEqual(before,((parent/'job.json').read_bytes(),ledger.read_bytes()))


if __name__=='__main__':unittest.main()
