#!/usr/bin/env python3
"""Finite Studio crawl using the Go fetcher and Decisions-gated link expansion."""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import time

from decisions_classifier import APIUnavailable, BudgetExhausted, DecisionsCache, HeldDecision
from discovery import atomic_json, file_hash, now_iso
from focused_frontier import DIRECTORY_HOSTS, Frontier, Policy, admitted, profile_key

STOP = False


def stop(signum, frame):
    global STOP
    STOP = True


def exposure(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        prior = db.execute('SELECT prior FROM settings WHERE id=1').fetchone()[0]
        used = db.execute("SELECT coalesce(sum(cost_cents + CASE WHEN status IN ('submitting','uncertain') THEN reservation ELSE 0 END),0) FROM requests WHERE imported=0").fetchone()[0]
        return prior + used


def carry_frontier(root, previous, policy, cache):
    """Copy completed evidence into a separately authorised continuation stage."""
    previous = Path(previous).resolve()
    old_job = json.loads((previous / 'job.json').read_text())
    old_status = json.loads((previous / 'status.json').read_text())
    if old_status.get('status') != 'complete' or Path(old_job['cache']).resolve() != Path(cache).resolve():
        raise ValueError('Only a completed stage using the same paid cache may continue')
    policy.validate()
    for key, value in old_job['policy'].items():
        if key not in ('max_pages', 'max_sites', 'directory_per_site') and policy.__dict__.get(key) != value:
            raise ValueError('A continuation must preserve geography and recursion gates')
    target = Path(root) / 'frontier.sqlite'
    if target.exists():
        raise ValueError('Never overwrite an existing frontier')
    with closing(sqlite3.connect((previous / 'frontier.sqlite').as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
            with destination:
                destination.execute('UPDATE policy SET value=? WHERE id=1', (json.dumps(policy.__dict__, sort_keys=True),))
                destination.execute('''UPDATE pages SET state='pending',reason=NULL WHERE state='held'
                    AND reason IN ('Stage ended before classification','Deadline reached before classification')
                    AND evidence IS NOT NULL''')
    return {'continuedFrom': str(previous), 'previousDeadlineUnix': old_job['deadlineUnix'],
            'initialAttempts': old_status['attempts']}


def prepare(root, index_path, cache, crawler, history, hours, pages, sites, source_sha,
            previous=None, directory_pages=200, workers=1, baseline=None):
    root = Path(root).resolve()
    if not 0 < hours <= 6 or not 1 <= workers <= 4 or root.exists():
        raise ValueError('Prepare a new finite stage; never reset an existing deadline')
    history = Path(history).resolve()
    prior = exposure(history / 'paid.sqlite')
    if prior != 6635:
        raise ValueError('Discovery exposure changed: reconcile before reserving classification budget')
    index_path = Path(index_path).resolve()
    with closing(sqlite3.connect(index_path.as_uri() + '?mode=ro', uri=True)) as index:
        config = json.loads(index.execute('SELECT value FROM research_config WHERE id=1').fetchone()[0])
        rows = index.execute("SELECT url_key,url,context FROM upstream_urls WHERE state IN ('pending','error')").fetchall()
        sources = {key: query for key, query in index.execute('''SELECT s.url_key,min(o.query_key) FROM url_sources s
            JOIN search_observations o ON o.run_dir=s.run_dir AND o.request_key=s.request_key
            WHERE o.receipt_verified=1 AND o.status='succeeded' GROUP BY s.url_key''')}
    towns = [t['name'] for t in config['towns']]
    root.mkdir(parents=True, mode=0o700)
    policy = Policy(towns, max_pages=pages, max_sites=sites, directory_per_site=directory_pages)
    continuation = carry_frontier(root, previous, policy, cache) if previous else {}
    known_profiles = set()
    if baseline:
        with Path(baseline).open(newline='') as stream:
            for record in csv.DictReader(stream):
                known_profiles.update(key for url in record.get('sourceUrls', '').split('; ')
                                      if (key := profile_key(url)))
    frontier = Frontier(root / 'frontier.sqlite', policy, known_profiles=known_profiles)
    try:
        initial = frontier.summary()
        # Re-open link extraction, not provider submissions. Larger directory
        # allowances can admit profile links that the preceding stage dropped.
        for saved in frontier.db.execute("SELECT key,evidence,decision FROM pages WHERE state='classified'").fetchall():
            frontier.complete(saved['key'], json.loads(saved['evidence']), json.loads(saved['decision']))
        for key, url, context in rows:
            if profile_key(url) in known_profiles:
                continue
            frontier.add(url, json.loads(context), group=sources.get(key, 'cached-search'),
                         provenance={'keywordIndex': str(index_path), 'upstreamUrlKey': key})
        # Reuse saved Go extraction before any new website request. Receipt
        # provenance remains attached even when the page redirects elsewhere.
        reused = 0
        for checkpoint in sorted(history.glob('crawls/*/crawl.json')):
            digest = file_hash(checkpoint)
            for result in json.loads(checkpoint.read_text()).get('results', []):
                evidence = result.get('evidence') or {}
                if not evidence.get('text'):
                    continue
                url = admitted(result.get('node', {}).get('url'))
                if not url:
                    continue
                row = frontier.db.execute('SELECT key FROM pages WHERE url=?', (url,)).fetchone()
                if row:
                    with frontier.db:
                        frontier.db.execute('UPDATE pages SET evidence=coalesce(evidence,?),provenance=? WHERE key=?',
                            (json.dumps(evidence), json.dumps({'checkpoint': str(checkpoint), 'checkpointSha256': digest,
                                                            'upstreamUrlKey': row['key']}), row['key']))
                    reused += 1
        started = time.time()
        job = {'version': 1, 'startedAt': now_iso(), 'deadlineUnix': started + hours * 3600,
               'policy': policy.__dict__, 'keywordIndex': str(index_path), 'cache': str(Path(cache).resolve()),
               'crawler': str(Path(crawler).resolve()), 'crawlerSha256': file_hash(crawler),
               'paidLedger': str(history / 'paid.sqlite'), 'priorExposureCents': prior,
               'classificationCapNano': 3_000_000_000, 'sourceSha': source_sha,
               'sourceDir': str(Path(__file__).resolve().parent),
               'reusedEvidencePages': reused, 'fetchWorkers': workers,
               'knownProfileKeys': sorted(known_profiles),
               'initialLocalTutorDomains': initial['localTutorDomains'],
               'initialLocalProfileCandidates': initial['localProfileCandidates'],
               **continuation, 'outreachEligible': False}
        if baseline:
            job['baselineProfileExport'] = str(Path(baseline).resolve())
            job['baselineProfileExportSha256'] = file_hash(baseline)
        atomic_json(root / 'job.json', job)
        atomic_json(root / 'status.json', {'status': 'prepared', **frontier.summary(), 'reusedEvidencePages': reused})
        return job
    finally:
        frontier.close()


def fetch(job, root, row):
    if row['evidence']:
        return json.loads(row['evidence'])
    folder = root / 'fetches' / row['key']
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    checkpoint = folder / 'crawl.json'
    directory = row['site'] in DIRECTORY_HOSTS
    if directory:
        # Existing Go discovery intentionally excludes marketplace domains from
        # contact candidates. Its research link audit can inspect them safely
        # without changing that outreach boundary or duplicating the fetcher.
        checkpoint = folder / 'audit.json'
        sources = folder / 'sources.json'
        atomic_json(sources, [{'url': row['url'], 'kind': 'professional_directory'}])
        args = [job['crawler'], 'link-audit', '--sources', str(sources), '--out', str(folder), '--checks', '1']
        if checkpoint.exists(): args += ['--resume']
    else:
        args = [job['crawler'], 'run', '--state', str(checkpoint), '--pages', '1', '--workers', '1']
        if checkpoint.exists():
            args += ['--resume']
        else:
            seeds = folder / 'seeds.txt'
            seeds.write_text(row['url'] + '\n')
            args += ['--seeds', str(seeds), '--depth', '0', '--branch-sites', '0', '--sites', '1', '--pages-per-site', '1']
    # Runtime keys stay in the API process, never in the Go crawler environment.
    environment = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'LANG', 'TMPDIR') if k in os.environ}
    with (folder / 'stderr.log').open('a') as stderr:
        child = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr, env=environment)
        try:
            child.wait(timeout=max(.1, min(45, job['deadlineUnix'] - time.time())))
        except subprocess.TimeoutExpired:
            child.send_signal(signal.SIGTERM)
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    if not checkpoint.exists():
        raise ValueError('Go fetch did not produce a checkpoint')
    state = json.loads(checkpoint.read_text())
    results = state.get('pages' if directory else 'results', [])
    if not results:
        raise ValueError('Go fetch checkpoint contains no result')
    result = results[0]
    if result.get('error'):
        raise ValueError('Go fetch: ' + str(result['error'])[:200])
    evidence = ({'url': row['url'], 'title': result.get('title'), 'text': result.get('text'),
                 'links': result.get('links', []), 'linkChecks': state.get('checks', []),
                 'textSha256': hashlib.sha256(result.get('text', '').encode()).hexdigest()}
                if directory else result.get('evidence') or {})
    if not evidence.get('text'):
        raise ValueError('No extracted page text')
    evidence['fetchCheckpointSha256'] = file_hash(checkpoint)
    return evidence


def status(root, job, frontier, cache, state='running', reason=None):
    summary = {'status': state, 'updatedAt': now_iso(), 'ownerPid': os.getpid(),
               'deadlineUnix': job['deadlineUnix'], 'sourceSha': job['sourceSha'],
               'priorExposureUsd': job['priorExposureCents'] / 100, 'decisions': cache.summary(),
               **frontier.summary()}
    summary['sharedExposureUsd'] = summary['priorExposureUsd'] + summary['decisions']['spentUsd'] + summary['decisions']['heldUsd']
    summary['stageAttempts'] = summary['attempts'] - job.get('initialAttempts', 0)
    summary['continuedFrom'] = job.get('continuedFrom')
    if reason:
        summary['reason'] = reason
    atomic_json(root / 'status.json', summary)
    return summary


def export(root, frontier):
    # Owner and fifteen-minute watcher share this artifact. The shared atomic
    # writer uses a fixed temporary name, so fence their writes explicitly.
    with (root / 'export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return export_locked(root, frontier)


def export_locked(root, frontier):
    rows = []
    for row in frontier.db.execute("SELECT * FROM pages WHERE state='classified'"):
        result = json.loads(row['decision'])
        if row['reason'] in ('local tutor research candidate', 'local profile research candidate'):
            evidence = json.loads(row['evidence'])
            rows.append({'url': row['url'], 'title': evidence.get('title'), 'site': row['site'],
                         'sourceGroup': row['group_key'], 'rootId': row['root'], 'parentId': row['parent'],
                         'contentSha256': evidence.get('contentHash'), 'textSha256': evidence.get('textSha256'),
                         'classification': result,
                         'profileKey': profile_key(row['url']),
                         'profileUrlInBaseline': profile_key(row['url']) in frontier.known_profiles,
                         'outreachEligible': False, 'qualification': 'Unaudited page candidate, not a new person count'})
    atomic_json(root / 'research-candidates.json', rows)
    profiles = {}
    for row in rows:
        if row['profileKey']:
            profiles.setdefault(row['profileKey'], row)
    atomic_json(root / 'profile-candidates.json', list(profiles.values()))
    atomic_json(root / 'profile-candidates-not-in-baseline.json',
                [r for r in profiles.values() if not r['profileUrlInBaseline']])
    return rows


def cleanup(root, frontier):
    removed = []
    fetches = root / 'fetches'
    if fetches.exists():
        for folder in fetches.iterdir():
            row = frontier.db.execute('SELECT state FROM pages WHERE key=?', (folder.name,)).fetchone()
            if folder.is_dir() and row and row[0] in ('classified', 'held', 'error'):
                shutil.rmtree(folder)
                removed.append(str(folder))
    atomic_json(root / 'cleanup.json', {'removed': removed, 'retained': [str(root / 'frontier.sqlite'),
                str(root / 'research-candidates.json'), str(root / 'profile-candidates.json'), str(root / 'job.json')],
                'note': 'SQLite retains page evidence, hashes, decisions, lineage and errors; shared caches and ledgers retained'})


def run(root):
    root = Path(root).resolve()
    job = json.loads((root / 'job.json').read_text())
    if exposure(job['paidLedger']) != job['priorExposureCents']:
        raise ValueError('Shared paid exposure changed; reconcile before continuing')
    if file_hash(job['crawler']) != job['crawlerSha256']:
        raise ValueError('Pinned Go crawler binary changed')
    with (root / 'run.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        previous = json.loads((root / 'status.json').read_text())
        if previous.get('status') in ('complete', 'api_unavailable', 'budget_exhausted'):
            return
        frontier = Frontier(root / 'frontier.sqlite', Policy(**job['policy']), job.get('knownProfileKeys', ()))
        cache = DecisionsCache(job['cache'], job['policy']['towns'], cap_nano=job['classificationCapNano'],
                               prior_exposure_cents=job['priorExposureCents'])
        try:
            frontier.recover()
            state, reason, processed, consecutive_held = 'complete', 'frontier exhausted or bounded page/site limit', 0, 0
            workers = job.get('fetchWorkers', 1)
            if type(workers) is not int or not 1 <= workers <= 4:
                raise ValueError('Invalid bounded fetch concurrency')
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending, last_start = {}, {}
                while True:
                    accepting = not STOP and time.time() < job['deadlineUnix'] and state == 'complete'
                    recent = {host for host, started in last_start.items() if time.monotonic() - started < 1}
                    if accepting:
                        hosts = recent | {r['site'] for r in pending.values()}
                        while len(pending) < workers:
                            row = frontier.claim(exclude_sites=hosts)
                            if not row:
                                break
                            hosts.add(row['site'])
                            last_start[row['site']] = time.monotonic()
                            pending[pool.submit(fetch, job, root, row)] = row
                        status(root, job, frontier, cache)
                    if not pending:
                        # A fast cached result may leave the same host cooling
                        # down. That is not an exhausted frontier.
                        if accepting and recent and frontier.db.execute(
                                "SELECT 1 FROM pages WHERE state='pending' LIMIT 1").fetchone():
                            time.sleep(.1)
                            continue
                        break
                    done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                    for future in done:
                        row, evidence = pending.pop(future), None
                        try:
                            evidence = future.result()
                            # Save text before submission; crash recovery can reuse it.
                            with frontier.db:
                                frontier.db.execute('UPDATE pages SET evidence=? WHERE key=?',
                                                    (json.dumps(evidence), row['key']))
                            if len(evidence.get('text', '').strip()) < 80:
                                frontier.hold(row['key'], 'Insufficient page text', evidence=evidence)
                                continue
                            if STOP or time.time() >= job['deadlineUnix'] or state != 'complete':
                                frontier.hold(row['key'], 'Stage ended before classification', evidence=evidence)
                                continue
                            frontier.complete(row['key'], evidence, cache.classify(evidence))
                            consecutive_held = 0
                        except (APIUnavailable, BudgetExhausted) as error:
                            frontier.hold(row['key'], type(error).__name__, evidence=evidence)
                            state = 'api_unavailable' if isinstance(error, APIUnavailable) else 'budget_exhausted'
                            reason = str(error)
                        except HeldDecision as error:
                            frontier.hold(row['key'], str(error), evidence=evidence)
                            consecutive_held += 1
                            if consecutive_held >= 5:
                                state, reason = 'api_unavailable', 'Five consecutive held API decisions; reconciliation required'
                        except (OSError, ValueError, subprocess.SubprocessError) as error:
                            frontier.hold(row['key'], str(error)[:250], state='error', evidence=evidence)
                        processed += 1
                        if processed % 25 == 0:
                            export(root, frontier)
                        status(root, job, frontier, cache)
                # API decisions and reservations stay on the owner thread. Slow
                # hosts occupy one fetch slot without stalling the other hosts.
            if STOP:
                state, reason = 'interrupted', 'Checkpoint retained with the original deadline'
            elif time.time() >= job['deadlineUnix']:
                reason = 'Original six-hour deadline reached'
            export(root, frontier)
            if state != 'interrupted':
                cleanup(root, frontier)
            status(root, job, frontier, cache, state, reason)
        finally:
            frontier.close()
            cache.close()


def watch(root, owner, monitor):
    root = Path(root)
    summary = json.loads((root / 'status.json').read_text())
    job = json.loads((root / 'job.json').read_text())
    frontier = Frontier(root / 'frontier.sqlite', Policy(**job['policy']), job.get('knownProfileKeys', ()))
    try:
        export(root, frontier)
        observation = {'observedAt': now_iso(), 'status': summary}
        process = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{owner}'], capture_output=True, text=True, timeout=10)
        observation['ownerLoaded'] = process.returncode == 0
        observation['running'] = 'state = running' in process.stdout
        with (root / 'health-checks.jsonl').open('a') as stream:
            stream.write(json.dumps(observation) + '\n')
        if summary['status'] in ('complete', 'api_unavailable', 'budget_exhausted'):
            for label in (owner, monitor):
                subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{label}'], capture_output=True, timeout=10)
        elif process.returncode == 0 and not observation['running']:
            # An expired interrupted stage still needs its final export/cleanup.
            # The owner's preserved deadline prevents any new requests.
            subprocess.run(['launchctl', 'kickstart', f'gui/{os.getuid()}/{owner}'], capture_output=True, timeout=10)
    finally:
        frontier.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run', 'watch'))
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--crawler', type=Path)
    parser.add_argument('--history', type=Path)
    parser.add_argument('--source-sha')
    parser.add_argument('--hours', type=float, default=6)
    parser.add_argument('--pages', type=int, default=5000)
    parser.add_argument('--sites', type=int, default=2000)
    parser.add_argument('--continue-from', type=Path)
    parser.add_argument('--directory-pages', type=int, default=200)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--owner-label')
    parser.add_argument('--monitor-label')
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(args.run_dir, args.index, args.cache, args.crawler, args.history,
                                 args.hours, args.pages, args.sites, args.source_sha,
                                 args.continue_from, args.directory_pages, args.workers, args.baseline)))
    elif args.command == 'run':
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        run(args.run_dir)
    else:
        watch(args.run_dir, args.owner_label, args.monitor_label)


if __name__ == '__main__': main()
