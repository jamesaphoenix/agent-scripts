"""Prepare the new paid owner, cached public-profile worker and shared classifier."""
import argparse
import json
from pathlib import Path
import re
import sqlite3

from discovery import atomic_json, file_hash
from incremental_discovery import prepare, validate_authorization, load_job
from prepare_profile_followon import prepare as prepare_profiles
from prepare_qualification_followon import prepare as prepare_qualification


def prepare_all(history, authorization, previous_qualification, previous_profiles, root, qualification, profiles):
    auth = json.loads(authorization.read_text())
    start, deadline = validate_authorization(auth)
    if not root.exists():
        prepare(root, authorization, history, previous_qualification, qualification, profiles)
    manifest, _ = load_job(root)
    if file_hash(authorization) != manifest['authorizationSha256']:
        raise ValueError('Preparation cannot change its authorised request')
    if not profiles.exists():
        prepare_profiles(previous_profiles, profiles, 12, 12000, 5000, source_run_dir=root,
                         requested_start=start, cumulative_ceiling=32000, allow_empty=True)
        job = json.loads((profiles / 'job.json').read_text())
        job.update(continueUntilDeadline=True, additionalWindowAuthorization=str((root / 'authorization.json').resolve()),
                   additionalWindowAuthorizationSha256=file_hash(root / 'authorization.json'))
        # The old queue contains global register pages. Preserve those for review,
        # and spend this window on URLs actually nominated by local sources.
        state = json.loads((profiles / 'state.json').read_text())
        towns = [t['name'] for t in manifest['towns']]
        def local_url(url):
            text = url.lower().replace('-', ' ').replace('%20', ' ')
            return any(re.search(r'\b' + re.escape(t.lower()) + r'\b', text) for t in towns)
        deferred = [url for url in state['queue'] if not local_url(url)]
        state['queue'] = [url for url in state['queue'] if local_url(url)]
        atomic_json(profiles / 'deferred-global-frontier.json', {'urls': deferred,
            'reason': 'No Bucks locality in route; retain for review, not an automatic new seed.'})
        atomic_json(profiles / 'state.json', state)
        job['seeds'] = list(state['queue'])
        atomic_json(profiles / 'job.json', job)
        status = json.loads((profiles / 'status.json').read_text())
        status['queue'] = len(state['queue'])
        atomic_json(profiles / 'status.json', status)
    if not qualification.exists():
        prepare_qualification(previous_qualification, qualification,
            Path(json.loads((previous_qualification / 'job.json').read_text())['input']), authorization, profiles)
        job = json.loads((qualification / 'job.json').read_text())
        job.update(discoveryCampaign=str(root.resolve()), keywordIndexPath=manifest['keywordIndexPath'],
                   sourceSha=manifest['sourceSha'], maximumCombinedAllowanceUsd=160)
        baseline = previous_qualification / 'exports/audited-subject-contacts.json'
        atomic_json(qualification / 'baseline/audited-contacts.json', json.loads(baseline.read_text()))
        snapshot = qualification / 'baseline/audited-contacts.json'
        job['knownContacts'] = {'path': str(snapshot.resolve()), 'sha256': file_hash(snapshot),
                                'count': len(json.loads(snapshot.read_text()))}
        # Revisit only changed owned context. Failed fetch claims and uncertain
        # paid decisions remain held; newly discovered candidates enter normally.
        proposal = json.loads((previous_qualification / 'verification/owned-context-revisits-proposal.json').read_text())
        keys = {c['candidateId'] for c in proposal['candidates']}
        with sqlite3.connect(qualification / 'candidates.sqlite') as db:
            db.execute('ATTACH DATABASE ? AS previous', (str(previous_qualification / 'candidates.sqlite'),))
            db.execute('''UPDATE candidates SET state=(SELECT p.state FROM previous.candidates p WHERE p.key=candidates.key)
                WHERE state='pending' AND key NOT IN (''' + ','.join('?' for _ in keys) + ')', tuple(keys))
        atomic_json(qualification / 'job.json', job)
    atomic_json(root / 'handoff.json', {'objective': 'Find more independent academic tutors in Bucks and Milton Keynes',
        'requestedAt': auth['requestedAt'], 'deadline': deadline.isoformat(), 'sourceSha': manifest['sourceSha'],
        'discoveryRoot': str(root), 'qualificationRoot': str(qualification), 'profileRoot': str(profiles),
        'newDiscoveryBudgetUsd': 50, 'existingClassificationBankUsd': 10, 'maximumSdkTurns': 0,
        'monitorIntervalSeconds': 900, 'outreachEligible': False,
        'stopConditions': ['immutable deadline', 'budget reservation cap', 'five consecutive provider failures',
                           'Decisions API or budget hold', 'no unsearched approved queries'],
        'cleanup': 'Remove only task-owned scratch at terminal completion. Preserve exports, caches, paid ledgers, holds and source awaiting PR review.'})
    return {'queries': len(json.loads((root / 'plan.json').read_text())), 'deadline': deadline.isoformat(),
            'discoveryRoot': str(root), 'qualificationRoot': str(qualification), 'profileRoot': str(profiles)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('history-root', 'authorization', 'previous-qualification', 'previous-profiles',
                 'run-dir', 'qualification-root', 'profile-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    a = parser.parse_args()
    print(json.dumps(prepare_all(a.history_root, a.authorization, a.previous_qualification,
        a.previous_profiles, a.run_dir, a.qualification_root, a.profile_root)))
