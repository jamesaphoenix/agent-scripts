"""Prepare an explicit ten-hour recovery using the unchanged USD50 paid ledger."""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
import time

from discovery import PaidCache, atomic_json, file_hash, now_iso
from incremental_discovery import (load_job, loaded_source_sha, process_live, recoverable_stop,
                                   validate_recovery_authorization)
from prepare_profile_followon import prepare as prepare_profiles
from prepare_qualification_followon import prepare as prepare_qualification


def seal_drained_profile(root, authorization):
    """A replacement may seal a stopped, empty owner; its old deadline is unchanged."""
    auth = json.loads(authorization.read_text())
    _, deadline = validate_recovery_authorization(auth)
    if (root.resolve() != Path(auth['previousProfileRoot']).resolve() or time.time() >= deadline.timestamp()
            or process_live('com.jud.' + root.name)):
        raise ValueError('Stop the authorised drained profile owner before sealing it')
    with (root / 'owner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status, state = [json.loads((root / p).read_text()) for p in ('status.json', 'state.json')]
        if state.get('queue') or status.get('queue'):
            raise ValueError('A nonempty frontier cannot be sealed as drained')
        before = file_hash(root / 'job.json')
        state.update(phase='stage_complete', updatedAt=now_iso(), stopReason='replaced_by_explicit_ten_hour_window')
        status.update(phase='stage_complete', observedAt=now_iso(), endedEarly=True,
                      stopReason='replaced_by_explicit_ten_hour_window')
        atomic_json(root / 'state.json', state)
        atomic_json(root / 'status.json', status)
        atomic_json(root / 'replacement.json', {'at': now_iso(), 'authorizationSha256': file_hash(authorization),
            'originalJobSha256': before, 'queue': 0, 'oldDeadlinePreserved': True})


def prepare(root, authorization, qualification, profiles):
    auth = json.loads(authorization.read_text())
    start, deadline = validate_recovery_authorization(auth)
    if time.time() >= deadline.timestamp():
        raise ValueError('The recovery window has ended')
    bank = Path(auth['bankRoot']).resolve()
    parent_qualification = Path(auth['previousQualificationRoot']).resolve()
    parent_profiles = Path(auth['previousProfileRoot']).resolve()
    original, _ = load_job(bank)
    if file_hash(bank / 'manifest.json') != auth['bankManifestSha256']:
        raise ValueError('Original bank manifest changed')
    with (bank / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if process_live('com.jud.' + bank.name):
            raise ValueError('Original paid owner is active')
        cache = PaidCache(bank, original['budgetCents'], original['priorExposureCents'])
        try:
            old = json.loads((bank / 'runtime.json').read_text())
            if not recoverable_stop(old, cache):
                raise ValueError('Only the proven settled search-error stop is recoverable here')
            if root.exists():
                current, _ = load_job(root)
                if current['authorizationSha256'] != file_hash(authorization):
                    raise ValueError('Recovery already exists with a different window')
            else:
                root.mkdir(parents=True, mode=0o700)
                shutil.copy2(authorization, root / 'authorization.json')
                for name in ('plan.json', 'discovery-scope.json'):
                    shutil.copy2(bank / name, root / name)
                job = dict(original, runId=root.name, hours=10, authorizedStartedAt=start.isoformat(),
                    authorizedDeadline=deadline.isoformat(), recoveryBankRoot=str(bank),
                    qualificationRoot=str(qualification.resolve()), profileRoot=str(profiles.resolve()),
                    authorizationSha256=file_hash(root / 'authorization.json'), monitorIntervalSeconds=1800,
                    sourceSha=loaded_source_sha())
                atomic_json(root / 'manifest.json', job)
                state = dict(old, phase='prepared', pid=0, observedAt=now_iso(), errors=[],
                    recovery={'previousRoot': str(bank), 'previousRuntimeSha256': file_hash(bank / 'runtime.json'),
                              'startingCompletedQueries': old['completedQueries'], 'newBudgetCents': 0},
                    consecutiveUncertain=0, outcomes={}, tacticHealth={})
                atomic_json(root / 'runtime.json', state)
                atomic_json(root / 'preparation.json', {'at': now_iso(), 'budget': cache.usage(),
                    'bankSettingsUnchanged': True, 'noNewPaidBank': True, 'paidCalls': 0,
                    'deadline': deadline.isoformat(), 'monitorIntervalSeconds': 1800})
        finally:
            cache.db.close()
    if not profiles.exists():
        prepare_profiles(parent_profiles, profiles, 10, 12000, 5000, source_run_dir=root,
                         requested_start=start, cumulative_ceiling=32000, allow_empty=True)
        job = json.loads((profiles / 'job.json').read_text())
        job.update(continueUntilDeadline=True, monitorIntervalSeconds=1800,
            additionalWindowAuthorization=str((root / 'authorization.json').resolve()),
            additionalWindowAuthorizationSha256=file_hash(root / 'authorization.json'))
        atomic_json(profiles / 'job.json', job)
    if not qualification.exists():
        parent_job = json.loads((parent_qualification / 'job.json').read_text())
        prepare_qualification(parent_qualification, qualification, Path(parent_job['input']), authorization, profiles)
        job = json.loads((qualification / 'job.json').read_text())
        job.update(discoveryCampaign=str(root.resolve()), keywordIndexPath=original['keywordIndexPath'],
                   sourceSha=loaded_source_sha(), sourceDir=str(Path(__file__).resolve().parent), monitorIntervalSeconds=1800)
        # Previously assessed inputs retain their exact states and fetch holds.
        # New receipts and verified changed context enter through the normal feeder.
        db = sqlite3.connect(qualification / 'candidates.sqlite')
        try:
            db.execute('ATTACH DATABASE ? AS previous', (str(parent_qualification / 'candidates.sqlite'),))
            with db:
                db.execute('''UPDATE candidates SET
                    state=(SELECT p.state FROM previous.candidates p WHERE p.key=candidates.key),
                    error=(SELECT p.error FROM previous.candidates p WHERE p.key=candidates.key)''')
        finally:
            db.close()
        atomic_json(qualification / 'job.json', job)
    atomic_json(root / 'handoff.json', {'objective': 'Recover tutor discovery and qualify independent Bucks/Milton Keynes tutors',
        'sourceSha': loaded_source_sha(), 'startedAt': start.isoformat(), 'deadline': deadline.isoformat(),
        'paidLedger': str(bank / 'paid.sqlite'), 'newBudgetCents': 0, 'originalBudgetCapCents': 5000,
        'remainingConservativeCentsAtHandoff': json.loads((root / 'preparation.json').read_text())['budget']['remainingCents'],
        'qualificationRoot': str(qualification), 'profileRoot': str(profiles), 'checksSeconds': 1800,
        'stopConditions': ['deadline', 'budget cap', 'account/auth/payment error', 'five uncertain submissions'],
        'sourceFailures': 'Empty results continue; repeated source/tactic errors cool down that tactic only.',
        'cleanup': 'Clean finished scratch; preserve ledgers, receipts, source caches, exports and unmerged review work.'})
    return {'deadline': deadline.isoformat(), 'newBudgetCents': 0, 'ledger': str(bank / 'paid.sqlite'),
            'monitorIntervalSeconds': 1800, 'sourceSha': loaded_source_sha()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['seal-profile', 'prepare'])
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--authorization', type=Path, required=True)
    parser.add_argument('--qualification-root', type=Path)
    parser.add_argument('--profile-root', type=Path)
    a = parser.parse_args()
    if a.command == 'seal-profile':
        seal_drained_profile(a.run_dir, a.authorization)
    else:
        print(json.dumps(prepare(a.run_dir, a.authorization, a.qualification_root, a.profile_root)))
