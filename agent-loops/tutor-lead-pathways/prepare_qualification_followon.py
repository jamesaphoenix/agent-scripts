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
STUDIO_FREE_INSTRUCTION = 'Could you continue this work on my Mac Studio? Please using herdr?'
TUTOR_GRAPH_INSTRUCTION = 'Go go for another 10-20 hours.'


def validate_tutor_graph(auth):
    start = datetime.datetime.fromisoformat(auth['requestedAt'])
    deadline = datetime.datetime.fromisoformat(auth['deadline'])
    if (auth.get('instruction') != TUTOR_GRAPH_INSTRUCTION
            or auth.get('precedingInstruction') != 'Could you continually improving the crawl and only adding extra tutor sites to our graph.'
            or auth.get('requestedAt') != '2026-10-17T17:46:36+00:00'
            or auth.get('deadline') != '2026-10-18T09:46:36+00:00'
            or start.tzinfo is None or deadline-start != datetime.timedelta(hours=16)
            or auth.get('durationHours') != 16
            or auth.get('newMoneyAuthorizedCents') != 0
            or auth.get('maximumRemainingSpendNano') != 3798775900
            or auth.get('sharedCombinedCeilingUsd') != 15
            or auth.get('paidDataForSeoSearchesAllowed') is not False
            or auth.get('decisionsClassificationAllowed') is not True
            or auth.get('maximumSdkTurns') != 0
            or auth.get('anthropicApiKeyAllowed') is not False
            or auth.get('outreachAllowed') is not False
            or auth.get('monitorIntervalSeconds') != 1800
            or auth.get('scopeSha256') != '626d1ea33c5d87366917a34f22f3fc8deed5fde3b6227503afa2ffc79d10f5c6'
            or auth.get('previousQualificationRoot') != '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/tutor-qualification-20261017-free-12h'
            or auth.get('previousProfileRoot') != '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/bucks-private-tutors-20261017-free-12h'):
        raise ValueError('Invalid tutor graph continuation authorization')
    return start, deadline


def validate_studio_free(auth):
    start = datetime.datetime.fromisoformat(auth['requestedAt'])
    deadline = datetime.datetime.fromisoformat(auth['deadline'])
    if (auth.get('instruction') != STUDIO_FREE_INSTRUCTION
            or auth.get('requestedAt') != '2026-10-17T09:03:13+00:00'
            or auth.get('deadline') != '2026-10-17T21:03:13+00:00'
            or start.tzinfo is None or deadline - start != datetime.timedelta(hours=12)
            or auth.get('newMoneyAuthorizedCents') != 0
            or auth.get('maximumRemainingSpendNano') != 3803813200
            or auth.get('paidDataForSeoSearchesAllowed') is not False
            or auth.get('maximumSdkTurns') != 0
            or auth.get('anthropicApiKeyAllowed') is not False
            or auth.get('outreachAllowed') is not False
            or auth.get('scopeSha256') != '626d1ea33c5d87366917a34f22f3fc8deed5fde3b6227503afa2ffc79d10f5c6'):
        raise ValueError('Invalid free Studio continuation authorization')
    return start, deadline


def prepare(previous, output, candidates, authorization, profile_root):
    previous, output = previous.resolve(), output.resolve()
    if output.exists():
        raise ValueError('Existing qualification stages must be resumed, never replaced')
    auth = json.loads(authorization.read_text())
    start = datetime.datetime.fromisoformat(auth['requestedAt'])
    deadline = datetime.datetime.fromisoformat(auth['deadline'])
    approved = auth.get('instruction') == 'Yes, start the new pass'
    hours = 12
    preserve_states = auth.get('instruction') == STUDIO_FREE_INSTRUCTION
    if auth.get('instruction') == TUTOR_GRAPH_INSTRUCTION:
        validate_tutor_graph(auth)
        if (str(previous) != auth['previousQualificationRoot']
                or str(profile_root.resolve()) != '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/bucks-private-tutors-20261017-tutor-graph-16h'):
            raise ValueError('Tutor graph stage ancestry changed')
        preserve_states, approved, hours = True, True, 16
    if auth.get('instruction') == STUDIO_FREE_INSTRUCTION:
        validate_studio_free(auth)
        approved = True
    if auth.get('instruction') == ADDITIONAL_DISCOVERY_INSTRUCTION:
        from incremental_discovery import validate_authorization
        validate_authorization(auth)
        approved = True
    from incremental_discovery import RECOVERY_INSTRUCTION, validate_recovery_authorization
    if auth.get('instruction') == RECOVERY_INSTRUCTION:
        validate_recovery_authorization(auth)
        approved, hours = True, 10
    if (not approved or start.tzinfo is None or deadline.tzinfo is None
            or deadline-start != datetime.timedelta(hours=hours)):
        raise ValueError('The new pass requires its explicit bounded window authorisation')
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
                    if not preserve_states:
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
            if preserve_states:
                reviewed = previous/'exports/audited-subject-contacts.json'
                if reviewed.exists():
                    (temporary/'exports').mkdir(mode=0o700)
                    shutil.copy2(reviewed, temporary/'exports/audited-subject-contacts.json')
            atomic_json(temporary/'status.json',{'phase':'prepared','observedAt':now_iso(),'ownerPid':0,
                'deadline':deadline.isoformat(),'decisions':status['decisions'],'inputRows':len(rows)})
            atomic_json(temporary/'preparation.json',{'observedAt':now_iso(),'inputRows':len(rows),
                'sourceInventorySha256':hashlib.sha256(raw).hexdigest(),'sharedCache':job['sharedCache'],
                'parentJobPreserved':True,'candidateStatesPreserved':preserve_states,
                'newPaidAllowanceCreated':False,'paidCalls':0})
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
