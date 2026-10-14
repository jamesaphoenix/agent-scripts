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


if __name__=='__main__':unittest.main()
