import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent-loops/tutor-lead-pathways'))
import discovery_10h_window as window
import directory_profiles
import prepare_qualification_followon as qualification
import studio_discovery_review_check as review
from qualify_inventory import create_database


class DiscoveryTenHourWindowTests(unittest.TestCase):
    def setUp(self):
        self.auth = json.loads((ROOT / 'tests/fixtures/tutor_discovery_10h_authorization.json').read_text())

    def test_exact_window_scope_budget_and_ancestry(self):
        start, deadline = window.validate(self.auth)
        self.assertEqual(deadline - start, datetime.timedelta(hours=10))
        for field, changed in [('deadline', '2026-10-18T23:06:54+00:00'),
                               ('newMoneyAuthorizedCents', 1),
                               ('previousProfileRoot', '/tmp/other'),
                               ('scopeChangesAllowed', True),
                               ('paidDataForSeoSearchesAllowed', True)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                window.validate({**self.auth, field: changed})

    def test_prepare_preserves_holds_and_inherited_accepted_export(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            parent = base/'previous'; parent.mkdir()
            child = base/'new'
            profile = base/'profile'; profile.mkdir()
            with sqlite3.connect(parent/'decisions.sqlite') as db:
                db.execute('CREATE TABLE decision_config(id INTEGER PRIMARY KEY,value TEXT)')
                db.execute('INSERT INTO decision_config VALUES(1,?)', ('sealed',))
            scope = parent/'discovery-scope.json'; scope.write_text('{}')
            inventory = base/'inventory.json'; inventory.write_text('[{"status":"research"}]')
            (parent/'job.json').write_text(json.dumps({'scope':str(scope),'input':str(inventory)}))
            (parent/'status.json').write_text(json.dumps({'phase':'prepared','decisions':{'remainingUsd':3.7987759}}))
            accepted = parent/'exports/automated-qualification/accepted.json'
            accepted.parent.mkdir(parents=True)
            accepted.write_text('[{"name":"inherited"}]')
            seeds = parent/'website-seeds'; seeds.mkdir(); (seeds/'seed.json').write_text('{}')
            db = create_database(parent/'candidates.sqlite')
            with db:
                db.execute("INSERT INTO candidates(key,candidate,state,error) VALUES('held','{}','fetch_held','403')")
                db.execute("INSERT INTO candidates(key,candidate,state) VALUES('negative','{}','excluded_irrelevant')")
                db.execute("INSERT INTO candidates(key,candidate,state) VALUES('uncertain','{}','decision_held')")
            db.close()
            auth = {**self.auth,'previousQualificationRoot':str(parent.resolve()),'profileRoot':str(profile.resolve()),'previousControlRoot':str(base)}
            (base/'final-export-manifest.json').write_text('{}')
            auth_path = base/'authorization.json'; auth_path.write_text(json.dumps(auth))
            with patch.object(window,'validate',return_value=(datetime.datetime.fromisoformat(self.auth['requestedAt']),datetime.datetime.fromisoformat(self.auth['deadline']))), \
                 patch.object(window,'verify_files',return_value=base/'manifest.json'):
                job = qualification.prepare(parent,child,inventory,auth_path,profile)
            self.assertEqual(job['authorizedDeadline'],self.auth['deadline'])
            self.assertEqual(json.loads((child/'exports/automated-qualification/accepted.json').read_text()),[{'name':'inherited'}])
            self.assertTrue((child/'website-seeds/seed.json').exists())
            self.assertFalse((parent/'run.lock').exists())
            with sqlite3.connect(child/'candidates.sqlite') as copied:
                self.assertEqual(dict(copied.execute('SELECT key,state FROM candidates')),
                                 {'held':'fetch_held','negative':'excluded_irrelevant','uncertain':'decision_held'})

    def test_runtime_admits_only_new_ten_hour_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            job = {'startedAt':self.auth['requestedAt'],
                   'deadlineUnix':datetime.datetime.fromisoformat(self.auth['deadline']).timestamp(),
                   'pageLimit':22979,'additionalWindowAuthorization':'/tmp/authorization.json'}
            stage = base/'bucks-private-tutors-20261018-free-10h';stage.mkdir()
            (stage/'job.json').write_text(json.dumps(job))
            with patch('configuration.profile_page_ceiling',return_value=32000), patch.object(directory_profiles,'run') as run:
                directory_profiles.main(['--run-dir',str(stage),'--hours','10','--pages','22979','--target','5000'])
                run.assert_called_once()
            with patch('configuration.profile_page_ceiling',return_value=32000), patch.object(directory_profiles,'run') as run:
                with self.assertRaises(SystemExit):
                    directory_profiles.main(['--run-dir',str(stage),'--hours','10.1','--pages','22979','--target','5000'])
                run.assert_not_called()

    def test_existing_final_snapshot_unloads_review_after_grace(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            (control/'authorization.json').write_text(json.dumps(self.auth))
            final = control/'inspections/20261018T220654Z.json'
            final.parent.mkdir()
            final.write_text('{}')
            deadline = datetime.datetime.fromisoformat(self.auth['deadline'])
            with patch.object(review,'CONTROL',control), patch.object(window,'validate'), \
                 patch.object(window,'verify_files'), patch.object(review.subprocess,'run') as run:
                review.check(deadline + datetime.timedelta(minutes=31))
            run.assert_called_once_with(['launchctl','bootout',f'gui/{review.os.getuid()}/{review.LABEL}'],
                                        capture_output=True,timeout=10)


if __name__ == '__main__':
    unittest.main()
