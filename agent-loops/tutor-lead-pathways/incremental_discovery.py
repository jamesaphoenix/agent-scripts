"""A separately authorised discovery bank; previous budgets and holds stay sealed."""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from configuration import campaign_window, loaded_source_sha
from discovery import (PaidCache, atomic_json, file_hash, now_iso, provider_request,
                       request_key, website_key)
from directory_profiles import admitted_url
from focused_frontier import link_signals
from keyword_expansion import SearchIndex, keyword_plan, search_identity, upstream_results
from prepare_campaign import archive_history, seed_batch

INSTRUCTION = 'My suggestion is have another $50 of budget and go find more tutors for the next 12 hours plz.'
EXTRA_CENTS = 5000
STOP = False


def validate_authorization(auth):
    start, deadline, _ = campaign_window({'hours': 12, 'authorizedStartedAt': auth['requestedAt'],
                                         'authorizedDeadline': auth['deadline']})
    if (auth.get('instruction') != INSTRUCTION or auth.get('additionalDiscoveryCents') != EXTRA_CENTS
            or auth.get('previousDiscoveryCapCents') != 10000 or auth.get('combinedDiscoveryCapCents') != 15000):
        raise ValueError('An explicit additional USD50 discovery authorisation is required')
    return start, deadline


def query_plan(towns):
    terms = ('11+ tutor', '11 plus tutor', 'tutor', 'private tutor', 'maths tutor', 'english tutor',
             'primary tutor', 'verbal reasoning tutor', 'non verbal reasoning tutor',
             'KS2 maths tutor', 'KS2 English tutor', 'literacy tutor', 'numeracy tutor',
             'SATs tutor', 'grammar school entrance tutor', '11 plus preparation',
             'one to one tuition', 'reading tutor', 'creative writing tutor',
             'home tuition', 'independent tutor', 'mathematics tuition', 'primary tuition')
    plan = keyword_plan(towns, terms)
    for item in plan:
        item['tactic'] = 'plain:' + item['lane']
    patterns = {
        'owner_sites': '"{town}" ("English tutor" OR "maths tutor" OR "11 plus") ("I am" OR "my name") -jobs -kumon',
        'local_adverts': '"{town}" ("tuition" OR "tutor") (inurl:newsletter OR filetype:pdf) -music',
        'local_directories': '"{town}" ("primary tutor" OR "11 plus" OR "maths tuition") ("directory" OR "community")',
        'public_profiles': '"{town}" ("maths" OR "English" OR "11 plus") (site:tutorhunt.com OR site:uktutors.com OR site:findtutors.co.uk)',
        'professional_registers': '"{town}" tutor (site:thetutorsassociation.wildapricot.org OR site:qualifiedtutor.org)',
        'small_businesses': '"{town}" ("private tuition" OR "independent tutor") -kumon -explorelearning -jobs -franchise',
    }
    for tactic, pattern in patterns.items():
        for town in towns:
            payload = {'keyword': pattern.format(town=town['name']), 'location_coordinate': town['coordinate'] + ',200',
                       'language_code': 'en', 'se_domain': 'google.co.uk', 'depth': 30}
            plan.append({'lane': 'organic', 'tactic': tactic, 'town': town['name'], 'payload': payload,
                         'key': request_key('organic', payload), 'generation': 0, 'priority': 100})
    return list({search_identity(q['lane'], q['payload']): q for q in plan}.values())


def prepare(root, authorization, history, previous_qualification, qualification_root, profile_root):
    root, history = root.resolve(), history.resolve()
    if root.exists():
        raise ValueError('Resume the prepared bank; never recreate its allowance')
    auth = json.loads(authorization.read_text())
    _, deadline = validate_authorization(auth)
    if time.time() >= deadline.timestamp():
        raise ValueError('Authorised discovery window has ended')
    # A second directory must not mint another bank for this same request.
    for existing in history.glob('tutor-incremental-*/authorization.json'):
        if ((existing.parent / 'paid.sqlite').exists() and (existing.parent / 'manifest.json').exists()
                and json.loads(existing.read_text()).get('requestedAt') == auth['requestedAt']):
            raise ValueError('This additional allowance already has an owner')
    prior, archives = archive_history(history)
    if (prior != auth['originalBankExposureCents'] or prior > 10000
            or prior + EXTRA_CENTS > auth['combinedDiscoveryCapCents']):
        raise ValueError('Inherited spending plus the new bank exceeds its combined cap')
    previous_job = json.loads((previous_qualification / 'job.json').read_text())
    towns = json.loads((archives[-1][0] / 'towns.json').read_text())
    if {t['name'] for t in towns} != set(previous_job['towns']):
        raise ValueError('Discovery cannot widen the qualification geography')
    with ExitStack() as locks:
        owners = []
        for old, _ in archives:
            lock = locks.enter_context((old / 'run.lock').open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest = json.loads((old / 'manifest.json').read_text())
            label = manifest.get('launchdLabels', {}).get('run', 'com.jud.' + old.name)
            if process_live(label):
                raise ValueError('Previous paid owner is still running')
            owners.append({'path': str(old), 'ownerLabel': label,
                           'manifestSha256': file_hash(old / 'manifest.json')})
        root.mkdir(mode=0o700)
        atomic_json(root / 'authorization.json', auth)
        cache = PaidCache(root, EXTRA_CENTS, 0)
        try:
            for old, rows in archives:
                for lane, payload, response, error in rows:
                    if response is not None:
                        cache.import_receipt(lane, payload, response)
                    else:
                        cache.import_unsettled(lane, payload, 'Original bank retains this hold: ' + old.name)
            index = SearchIndex(history / 'keyword-search.sqlite')
            try:
                plan = [q for q in index.unsearched(query_plan(towns)) if not cache.get(q['key'])]
                index.add_queries(plan, origin='additional-usd50:' + str(root))
            finally:
                index.close()
            atomic_json(root / 'plan.json', plan)
            atomic_json(root / 'discovery-scope.json', json.loads(Path(previous_job['scope']).read_text()))
            manifest = {'runId': root.name, 'authorizedStartedAt': auth['requestedAt'],
                'authorizedDeadline': auth['deadline'], 'hours': 12, 'budgetCents': EXTRA_CENTS,
                'priorExposureCents': 0, 'originalBankExposureCents': prior, 'combinedCapCents': 15000,
                'authorizationSha256': file_hash(root / 'authorization.json'), 'planSha256': file_hash(root / 'plan.json'),
                'scopeSha256': file_hash(root / 'discovery-scope.json'), 'originalOwners': owners,
                'keywordIndexPath': str(history / 'keyword-search.sqlite'), 'towns': towns,
                'qualificationRoot': str(qualification_root.resolve()), 'profileRoot': str(profile_root.resolve()),
                'sourceSha': loaded_source_sha(), 'maximumSdkTurns': 0, 'outreachEligible': False,
                'requestIntervalSeconds': 20, 'monitorIntervalSeconds': 900,
                'baselineContacts': len(json.loads((previous_qualification / 'exports/audited-subject-contacts.json').read_text())),
                'originalBankReconciliationApplied': False,
                'note': 'Only the new USD50 is available here. Old headroom and all original holds remain in the old bank.'}
            atomic_json(root / 'manifest.json', manifest)
            atomic_json(root / 'runtime.json', {'phase': 'prepared', 'pid': 0, 'observedAt': now_iso(),
                'tactics': {}, 'seenOwners': [], 'errors': [], 'completedQueries': 0, 'budget': cache.usage()})
        finally:
            cache.db.close()
    return {'queries': len(plan), 'deadline': auth['deadline'], 'newBankCents': EXTRA_CENTS,
            'priorExposureRetainedCents': prior}


def load_job(root):
    job = json.loads((root / 'manifest.json').read_text())
    for name, key in [('authorization.json', 'authorizationSha256'), ('plan.json', 'planSha256'),
                      ('discovery-scope.json', 'scopeSha256')]:
        if file_hash(root / name) != job[key]:
            raise ValueError('Immutable discovery input changed: ' + name)
    auth = json.loads((root / 'authorization.json').read_text())
    _, deadline = validate_authorization(auth)
    if (job['budgetCents'] != EXTRA_CENTS or job['priorExposureCents'] != 0 or job['combinedCapCents'] != 15000
            or job['originalBankExposureCents'] != auth['originalBankExposureCents']
            or not 0 <= job['originalBankExposureCents'] <= 10000
            or job['originalBankExposureCents'] + EXTRA_CENTS > 15000
            or job['authorizedDeadline'] != auth['deadline'] or job['authorizedStartedAt'] != auth['requestedAt']
            or job['maximumSdkTurns'] != 0):
        raise ValueError('The authorised bank or original deadline changed')
    return job, deadline.timestamp()


def process_live(label):
    result = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{label}'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        if 'Could not find service' in result.stderr:
            return False
        raise ValueError('Cannot verify launchd owner: ' + label)
    return bool(re.search(r'pid = [1-9]\d*', result.stdout))


def wake_qualification(job):
    root = Path(job['qualificationRoot'])
    status = json.loads((root / 'status.json').read_text())
    if status.get('phase') in ('deadline', 'budget_reached', 'api_unavailable'):
        return
    subprocess.run(['launchctl', 'kickstart', f'gui/{os.getuid()}/com.jud.{root.name}'],
                   capture_output=True, timeout=10)


def choose_query(plan, cache, index, state):
    choices = [q for q in index.unsearched(plan) if not cache.get(q['key'])]
    count = state['completedQueries']
    def score(q):
        stats = state['tactics'].get(q['tactic'], {})
        attempts = stats.get('requests', 0)
        novelty = (stats.get('newRelevantOwners', 0) + 1) / (attempts + 2)
        # Explore each tactic, then allocate most requests to useful sources.
        return (0 if attempts == 0 else 1, attempts if count % 10 == 0 else -novelty,
                q.get('priority', 0) * -1, q['key'])
    return min(choices, key=score) if choices else None


def observe_results(root, job, item, envelope, state, seen):
    results = list(upstream_results(envelope)) if envelope else []
    novel, profiles = set(), []
    towns = [t['name'] for t in job['towns']]
    for url, context in results:
        cue, _ = link_signals(url, context, towns)
        owner = website_key(url)
        if cue >= .9 and owner and owner not in seen:
            novel.add(owner)
        if cue >= .9 and admitted_url(url):
            profiles.append(url)
    seen.update(novel)
    stats = state['tactics'].setdefault(item['tactic'], {'requests': 0, 'newRelevantOwners': 0, 'resultUrls': 0})
    stats['requests'] += 1
    stats['newRelevantOwners'] += len(novel)
    stats['resultUrls'] += len(results)
    # A result nominates a page, never proves locality or identity. Keep branch bounds.
    if profiles:
        seed_batch(Path(job['profileRoot']), sorted(set(profiles))[:15])
    atomic_json(root / 'observations' / (item['key'] + '.json'), {'query': item,
        'observedAt': now_iso(), 'newRelevantOwners': len(novel), 'resultUrls': len(results),
        'qualification': 'Search nominations only; not verified tutor contacts', 'outreachEligible': False})


def stop(*_):
    global STOP
    STOP = True


def run(root):
    job, deadline = load_job(root)
    if time.time() >= deadline:
        return
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (root / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for old in job['originalOwners']:
            if file_hash(Path(old['path']) / 'manifest.json') != old['manifestSha256'] or process_live(old['ownerLabel']):
                raise ValueError('Previous spending owner changed or restarted')
        cache, index = PaidCache(root, EXTRA_CENTS, 0), SearchIndex(job['keywordIndexPath'])
        state = json.loads((root / 'runtime.json').read_text())
        seen = set(state['seenOwners'])
        if not state.get('baselineOwnersCaptured'):
            seen.update(website_key(r[0]) for r in index.db.execute('SELECT url FROM upstream_urls'))
            state['baselineOwnersCaptured'] = True
        state.update(phase='running', pid=os.getpid(), loadedSourceSha=loaded_source_sha(), observedAt=now_iso())
        atomic_json(root / 'runtime.json', state)
        plan = json.loads((root / 'plan.json').read_text())
        consecutive_failures = 0
        try:
            cache.recover_interrupted()
            index.sync_run(root, job['towns'])
            while not STOP and time.time() < deadline - 120:
                item = choose_query(plan, cache, index, state)
                if not item:
                    state['phase'] = 'search_plan_exhausted'
                    break
                reserve = 2 if item['lane'] == 'maps' else 5
                if cache.usage()['remainingCents'] < reserve:
                    state['phase'] = 'budget_reached'
                    break
                started = time.monotonic()
                envelope = provider_request(cache, item['lane'], item['payload'], maximum_cents=reserve)
                row = cache.get(item['key'])
                consecutive_failures = 0 if row and row['status'] == 'succeeded' else consecutive_failures + 1
                observe_results(root, job, item, envelope if row and row['status'] == 'succeeded' else None, state, seen)
                state['completedQueries'] += 1
                index.sync_run(root, job['towns'])
                wake_qualification(job)
                state.update(observedAt=now_iso(), seenOwners=sorted(seen), budget=cache.usage(),
                    combinedExposureCents=job['originalBankExposureCents'] + cache.usage()['exposureCents'])
                atomic_json(root / 'runtime.json', state)
                if consecutive_failures >= 5:
                    state['phase'] = 'api_unavailable'
                    break
                remaining = job['requestIntervalSeconds'] - (time.monotonic() - started)
                while remaining > 0 and not STOP and time.time() < deadline - 120:
                    time.sleep(min(remaining, 1))
                    remaining = job['requestIntervalSeconds'] - (time.monotonic() - started)
            if state['phase'] == 'running':
                state['phase'] = 'interrupted' if STOP else 'deadline'
        except Exception as error:
            state['phase'] = 'interrupted'
            state['errors'].append({'at': now_iso(), 'type': type(error).__name__, 'message': str(error)[:240]})
            raise
        finally:
            state.update(observedAt=now_iso(), budget=cache.usage(), seenOwners=sorted(seen))
            atomic_json(root / 'runtime.json', state)
            cache.db.close()
            index.close()


def watch(root):
    job, deadline = load_job(root)
    owner = 'com.jud.' + root.name
    state = json.loads((root / 'runtime.json').read_text())
    live = process_live(owner)
    prior = json.loads((root / 'monitor.json').read_text()) if (root / 'monitor.json').exists() else {}
    terminal = time.time() >= deadline or state['phase'] in ('api_unavailable', 'budget_reached', 'search_plan_exhausted', 'deadline')
    report = {'observedAt': now_iso(), 'processLive': live, 'terminal': terminal, 'runtime': state,
              'recoveries': prior.get('recoveries', 0), 'deadline': job['authorizedDeadline']}
    if not terminal and not live and report['recoveries'] < 3:
        subprocess.run(['launchctl', 'kickstart', f'gui/{os.getuid()}/{owner}'], capture_output=True, timeout=10)
        report['recoveries'] += 1
    if not terminal:
        wake_qualification(job)
    atomic_json(root / 'monitor.json', report)
    atomic_json(root / 'monitor' / (now_iso().replace(':', '-') + '.json'), report)
    if terminal and not live:
        atomic_json(root / 'cleanup.json', {'observedAt': now_iso(), 'removed': [],
            'retained': ['paid.sqlite', 'receipts', 'observations', 'manifest.json', 'authorization.json',
                         'plan.json', 'runtime.json', 'monitor', 'source checkout awaiting PR review'],
            'note': 'No disposable downloads were created. Preserve all paid and source evidence.'})
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{owner}-watch'], capture_output=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'run', 'launch', 'watch'])
    parser.add_argument('--run-dir', type=Path, required=True)
    for name in ('authorization', 'history-root', 'previous-qualification', 'qualification-root', 'profile-root'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(args.run_dir, args.authorization, args.history_root,
            args.previous_qualification, args.qualification_root, args.profile_root)))
    elif args.command == 'launch':
        _, deadline = load_job(args.run_dir)
        if time.time() < deadline:
            from launch import runner_environment
            environment = runner_environment(os.environ)
            os.execve(sys.executable, [sys.executable, __file__, 'run', '--run-dir', str(args.run_dir)], environment)
    elif args.command == 'run':
        run(args.run_dir)
    else:
        watch(args.run_dir)
