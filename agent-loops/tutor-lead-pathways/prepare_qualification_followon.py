"""Prepare an authorised context pass while retaining the shared paid ledger."""
import argparse
from contextlib import closing
import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from discovery import atomic_json, file_hash, now_iso
from qualification_context import cache_path

ADDITIONAL_DISCOVERY_INSTRUCTION = 'My suggestion is have another $50 of budget and go find more tutors for the next 12 hours plz.'


def prepare(previous, output, candidates, authorization, profile_root):
    previous, output = previous.resolve(), output.resolve()
    if output.exists():
        raise ValueError('Existing qualification stages must be resumed, never replaced')
    auth = json.loads(authorization.read_text())
    start = datetime.datetime.fromisoformat(auth['requestedAt'])
    deadline = datetime.datetime.fromisoformat(auth['deadline'])
    approved = auth.get('instruction') == 'Yes, start the new pass'
    if auth.get('instruction') == ADDITIONAL_DISCOVERY_INSTRUCTION:
        from incremental_discovery import validate_authorization
        validate_authorization(auth)
        approved = True
    if (not approved or start.tzinfo is None or deadline.tzinfo is None
            or deadline-start != datetime.timedelta(hours=12)):
        raise ValueError('The new pass requires its explicit twelve-hour authorisation')
    with (previous/'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        status = json.loads((previous/'status.json').read_text())
        if status['phase'] not in ('complete', 'deadline'):
            raise ValueError('Previous qualification must be terminal')
        parent = json.loads((previous/'job.json').read_text())
        ledger = cache_path(previous, parent)
        with closing(sqlite3.connect(ledger.as_uri()+'?mode=ro', uri=True)) as db:
            config = db.execute('SELECT value FROM decision_config WHERE id=1').fetchone()[0]
        raw = candidates.read_bytes()
        if len(raw)>64*1024*1024:
            raise ValueError('Candidate inventory exceeded its bound')
        rows = [r for r in json.loads(raw) if r.get('status')=='research' and not r.get('sourceOnly')]
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.'+output.name+'-', dir=output.parent))
        try:
            atomic_json(temporary/'input.json', rows)
            shutil.copy2(Path(parent['scope']),temporary/'discovery-scope.json')
            shutil.copy2(authorization,temporary/'window-authorization.json')
            with closing(sqlite3.connect((previous/'candidates.sqlite').as_uri()+'?mode=ro',uri=True)) as source:
                with closing(sqlite3.connect(temporary/'candidates.sqlite')) as target:
                    source.backup(target)
                    with target:
                        target.execute("UPDATE candidates SET state='pending',error=NULL WHERE state IN "
                                       "('uncertain','subject_review','locality_review','needs_owned_page_review','fetch_held')")
            job = dict(parent, input=str(output/'input.json'),scope=str(output/'discovery-scope.json'),
                inputSha256=file_hash(temporary/'input.json'),scopeSha256=file_hash(temporary/'discovery-scope.json'),
                deadlineUnix=deadline.timestamp(),authorizedDeadline=deadline.isoformat(),startedAt=start.isoformat(),
                sharedCache={'path':str(ledger),'configurationSha256':hashlib.sha256(config.encode()).hexdigest()},
                fetchRoots=list(dict.fromkeys([str(previous),*parent.get('fetchRoots',[])])),
                profileRoots=list(dict.fromkeys([str(profile_root.resolve()),*parent.get('profileRoots',[])])),
                expandOwnedContext=True,refreshContext=True,
                contextOwnerLabel='com.jud.'+profile_root.name,
                windowAuthorization=str(output/'window-authorization.json'),
                windowAuthorizationSha256=file_hash(temporary/'window-authorization.json'),
                previousStage={'path':str(previous),'jobSha256':file_hash(previous/'job.json'),
                               'inputSha256':file_hash(Path(parent['input']))},
                previousDecisions=status['decisions'])
            atomic_json(temporary/'job.json',job)
            atomic_json(temporary/'status.json',{'phase':'prepared','observedAt':now_iso(),'ownerPid':0,
                'deadline':deadline.isoformat(),'decisions':status['decisions'],'inputRows':len(rows)})
            atomic_json(temporary/'preparation.json',{'observedAt':now_iso(),'inputRows':len(rows),
                'sourceInventorySha256':hashlib.sha256(raw).hexdigest(),'sharedCache':job['sharedCache'],
                'parentJobPreserved':True,'newPaidAllowanceCreated':False,'paidCalls':0})
            temporary.rename(output)
        except BaseException:
            shutil.rmtree(temporary)
            raise
    return job


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('previous','run-dir','candidates','authorization','profile-run-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    job=prepare(args.previous,args.run_dir,args.candidates,args.authorization,args.profile_run_dir)
    print(json.dumps({'deadline':job['authorizedDeadline'],'sharedCache':job['sharedCache'],'paidCalls':0}))
