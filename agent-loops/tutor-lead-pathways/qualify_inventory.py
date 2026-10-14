#!/usr/bin/env python3
"""Finite, resumable Decisions classification of an already discovered inventory."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import time
from urllib.parse import urlsplit

from candidate_review import CachedDecisions, candidate_urls, page_index
from decisions_classifier import DecisionsCache, BudgetExhausted, APIUnavailable, HeldDecision, questions
from discovery import atomic_json, file_hash, now_iso
from focused_frontier import admitted, site_identity
from focused_run import fetch
from lead_scope import load_scope, scope_prompt, matching_page_subjects
from configuration import loaded_source_sha

VERSION = 'bucks-owned-candidate-v2'
STOP = False


def stop(*_):
    global STOP
    STOP = True


def owned_questions(towns, scope):
    result = questions(towns)
    context = ('Evaluate only the supplied candidate and their own advertised offer. '
               'Other tutors, testimonials, navigation, neighbouring adverts and search origin are not candidate evidence. ')
    for question in result:
        question['instructions'] = context + question['instructions']
    result[0]['instructions'] += (' A named public tutor profile represents the individual when its own fields identify them. '
        'A small owner-run tuition business or centre can be independent; exclude schools, franchises and large chains. '
        'An empty, blocked or ambiguous page is uncertain. Do not infer independence from its name.')
    result[1]['instructions'] = context + scope_prompt(scope) + (
        ' Is there an explicit own offer of these requested subjects or primary tuition? '
        'Music-only and other-subject-only offers fail. A business name or search snippet alone cannot establish subjects.')
    result[2]['instructions'] += (' Require the candidate\'s own service coverage or advertised address. '
        'An address in a receipt-backed business listing can establish advertised locality, but query location cannot. '
        'National online tuition and a grammar-school admissions discussion alone do not establish local service.')
    result[4]['instructions'] += ' An age band saying 11+ (secondary) is not an eleven-plus tuition offer.'
    return result


def input_evidence(candidate, pages):
    metadata = {k: candidate.get(k) for k in ('name', 'aliases', 'website', 'sourceUrl', 'advertisedAddress', 'category', 'description')}
    metadata['aliases'] = (metadata['aliases'] or [])[:10]
    observations = [{'url': p['url'], 'title': p.get('title'), 'text': p.get('text', '')[:4000]}
                    for p in pages[:3]]
    text = json.dumps({'candidate': metadata, 'ownedPageObservations': observations}, ensure_ascii=False)
    return {'url': candidate.get('website') or candidate.get('sourceUrl') or '',
            'title': candidate.get('name', ''), 'description': 'Classify this candidate using the supplied public evidence.',
            'text': text}


def outcome(decision, pages, scope):
    if decision['kindProbability'] < .8 or decision['kind'] == 'uncertain':
        return 'uncertain'
    if decision['kind'] in ('institution', 'irrelevant'):
        return 'excluded_' + decision['kind']
    if decision['kind'] == 'directory':
        return 'source_to_expand'
    if decision['outside'] >= .8:
        return 'outside_area'
    if decision['niche'] < .85:
        return 'subject_review'
    if decision['local'] < .85:
        return 'locality_review'
    if not pages:
        return 'needs_owned_page_review'
    if not any(matching_page_subjects(p.get('text', ''), scope) for p in pages):
        return 'subject_review'
    return 'tutor_research_candidate'


def recheck_saved_outcomes(db, scope):
    """Reapply local disposition rules to settled decisions without API requests."""
    states = ('uncertain', 'excluded_institution', 'excluded_irrelevant', 'source_to_expand',
              'outside_area', 'subject_review', 'locality_review', 'needs_owned_page_review',
              'tutor_research_candidate')
    placeholders = ','.join('?' for _ in states)
    rows = db.execute(f'SELECT key,state,evidence,decision FROM candidates '
                      f'WHERE state IN ({placeholders}) AND decision IS NOT NULL', states).fetchall()
    with db:
        for row in rows:
            state = outcome(json.loads(row['decision']), json.loads(row['evidence'] or '[]'), scope)
            if state != row['state']:
                db.execute('UPDATE candidates SET state=?,updated_at=CURRENT_TIMESTAMP WHERE key=?',
                           (state, row['key']))


def create_database(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''CREATE TABLE IF NOT EXISTS candidates (
        key TEXT PRIMARY KEY, candidate TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        evidence TEXT, decision TEXT, error TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS fetch_claims (url TEXT PRIMARY KEY, state TEXT NOT NULL,
        evidence TEXT, error TEXT, started_at REAL NOT NULL);''')
    return db


def snapshot(root, job, db, cache, phase, reason=None):
    counts = dict(db.execute('SELECT state,count(*) FROM candidates GROUP BY state').fetchall())
    results = []
    for row in db.execute("SELECT * FROM candidates WHERE state NOT IN ('pending','fetching') ORDER BY key"):
        results.append({'candidateId': row['key'], 'candidate': json.loads(row['candidate']), 'status': row['state'],
            'decision': json.loads(row['decision']) if row['decision'] else None,
            'pageEvidence': [{'url': e['url'], 'textSha256': hashlib.sha256(e.get('text', '').encode()).hexdigest()}
                             for e in json.loads(row['evidence'] or '[]')],
            'error': row['error'], 'outreachEligible': False})
    atomic_json(root / 'candidate-classifications.json', results)
    atomic_json(root / 'tutor-research-candidates.json', [r for r in results if r['status']=='tutor_research_candidate'])
    status = {'observedAt': now_iso(), 'phase': phase, 'ownerPid': os.getpid(), 'sourceSha': job['loadedSourceSha'],
        'counts': counts, 'decisions': cache.summary(), 'deadline': job['authorizedDeadline'],
        'additionalClassificationCapUsd': job['authorization']['additionalCapCents'] / 100,
        'outreachEligible': 0, 'newAuditedContacts': 0, 'reason': reason,
        'qualification': 'Model-assessed research candidates. Owned identities, contact routes and duplicate matching remain required before audited contact export.'}
    atomic_json(root / 'status.json', status)
    return status


def run(root):
    global STOP
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    job = json.loads((root / 'job.json').read_text())
    for key, pathkey in [('inputSha256', 'input'), ('scopeSha256', 'scope'), ('crawlerSha256', 'crawler')]:
        if file_hash(Path(job[pathkey])) != job[key]:
            raise ValueError('Immutable classification input changed: ' + pathkey)
    job['loadedSourceSha'] = loaded_source_sha()
    scope = load_scope(Path(job['scope']).parent)
    with (root / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        db = create_database(root / 'candidates.sqlite')
        cache = DecisionsCache(root / 'decisions.sqlite', job['towns'], cap_nano=job['authorization']['additionalCapCents'] * 10_000_000,
            prior_exposure_cents=10000, additional_authorization=job['authorization'],
            question_set=owned_questions(job['towns'], scope), prompt_version=VERSION)
        phase,reason='running',None
        last_snapshot=0
        try:
            with db:
                db.execute("UPDATE candidates SET state='fetch_held',error='Previous fetch interrupted; not replayed' WHERE state='fetching'")
                for candidate in json.loads(Path(job['input']).read_text()):
                    if candidate.get('status') != 'research' or candidate.get('sourceOnly'):
                        continue
                    key = hashlib.sha256(json.dumps([candidate.get('name'),candidate.get('website'),candidate.get('sourceUrl'),
                        sorted(candidate.get('identifiers', []))],sort_keys=True).encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO candidates(key,candidate) VALUES(?,?)',(key,json.dumps(candidate)))
            recheck_saved_outcomes(db, scope)
            index = page_index([Path(p) for p in job['frontiers']], [Path(p) for p in job['crawlRoots']])
            # A verified saved observation can resolve a failed fetch without
            # retrying that request or resetting any paid submission.
            with db:
                for row in db.execute("SELECT key,candidate FROM candidates WHERE state='fetch_held' AND decision IS NULL AND (evidence IS NULL OR evidence='[]')").fetchall():
                    if any(url in index for url in candidate_urls(json.loads(row['candidate']))):
                        db.execute("UPDATE candidates SET state='pending',error=NULL WHERE key=?",(row['key'],))
            api_failures = 0
            blocked_urls=set(job.get('blockedUrls',[]))
            legacy=CachedDecisions(Path(job['legacyCache']))
            def progress(force=False):
                nonlocal last_snapshot
                if force or time.monotonic()-last_snapshot>=10:
                    snapshot(root,job,db,cache,'running')
                    last_snapshot=time.monotonic()
            def complete(row, pages):
                nonlocal api_failures
                try:
                    decision = cache.classify(input_evidence(json.loads(row['candidate']), pages))
                    state = outcome(decision, pages, scope)
                    with db:
                        db.execute('UPDATE candidates SET state=?,evidence=?,decision=?,updated_at=CURRENT_TIMESTAMP WHERE key=?',
                            (state,json.dumps(pages),json.dumps(decision),row['key']))
                    api_failures = 0
                except BudgetExhausted:
                    raise
                except APIUnavailable:
                    raise
                except HeldDecision as error:
                    with db:
                        db.execute("UPDATE candidates SET state='decision_held',evidence=?,error=? WHERE key=?",
                            (json.dumps(pages),str(error),row['key']))
                    api_failures += 1
                    if api_failures >= 5:
                        raise APIUnavailable('Five consecutive held decisions; reconciliation required')
            pending_fetches = []
            for row in db.execute("SELECT * FROM candidates WHERE state='pending' ORDER BY key").fetchall():
                if STOP or time.time() >= job['deadlineUnix']:
                    break
                candidate = json.loads(row['candidate'])
                pages = [index[u] for u in candidate_urls(candidate) if u in index][:3]
                held=False
                for page in pages:
                    try:legacy.lookup(page)
                    except (ValueError,KeyError,TypeError):held=True
                if held:
                    with db:db.execute("UPDATE candidates SET state='decision_held',error='Legacy decision requires reconciliation' WHERE key=?",(row['key'],))
                    continue
                if pages:
                    complete(row,pages)
                else:
                    urls = [u for u in candidate_urls(candidate) if site_identity(u) != 'google.com']
                    if not urls:
                        complete(row, [])
                    elif urls[0] in blocked_urls:
                        with db:db.execute("UPDATE candidates SET state='fetch_held',error='Previously blocked or failed public source; not retried' WHERE key=?",(row['key'],))
                    else:
                        pending_fetches.append((row,urls[0]))
                progress()
            legacy.db.close()
            next_site, site_count, inflight = {}, Counter(), {}
            for claim in db.execute('SELECT url,started_at FROM fetch_claims'):
                site=site_identity(claim['url']);site_count[site]+=1
                next_site[site]=max(next_site.get(site,0),claim['started_at']+60)
            with ThreadPoolExecutor(max_workers=4) as pool:
                while pending_fetches or inflight:
                    if STOP or time.time() >= job['deadlineUnix']:
                        break
                    for entry in list(pending_fetches):
                        if len(inflight) >= 4:
                            break
                        row,url = entry;site=site_identity(url)
                        if time.time() < next_site.get(site,0):
                            continue
                        pending_fetches.remove(entry)
                        previous = db.execute('SELECT * FROM fetch_claims WHERE url=?',(url,)).fetchone()
                        if previous:
                            if previous['state']=='succeeded':
                                complete(row,[json.loads(previous['evidence'])])
                            else:
                                with db: db.execute("UPDATE candidates SET state='fetch_held',error='Previous fetch held' WHERE key=?",(row['key'],))
                            continue
                        if site_count[site] >= 8:
                            with db: db.execute("UPDATE candidates SET state='fetch_held',error='Eight-page site allowance reached' WHERE key=?",(row['key'],))
                            continue
                        site_count[site]+=1;next_site[site]=time.time()+60
                        with db:
                            db.execute("INSERT INTO fetch_claims VALUES(?,'submitting',NULL,NULL,?)",(url,time.time()))
                            db.execute("UPDATE candidates SET state='fetching' WHERE key=?",(row['key'],))
                        fetch_row={'url':url,'key':row['key'],'site':site,'evidence':None}
                        inflight[pool.submit(fetch,job,root,fetch_row)]=(row,url)
                    if not inflight:
                        time.sleep(1)
                        continue
                    done,_=wait(inflight,timeout=1,return_when=FIRST_COMPLETED)
                    for future in done:
                        row,url=inflight.pop(future)
                        try:
                            evidence=future.result()
                            with db: db.execute("UPDATE fetch_claims SET state='succeeded',evidence=? WHERE url=?",(json.dumps(evidence),url))
                        except Exception as error:
                            with db:
                                db.execute("UPDATE fetch_claims SET state='held',error=? WHERE url=?",(type(error).__name__,url))
                                db.execute("UPDATE candidates SET state='fetch_held',error=? WHERE key=?",(type(error).__name__,row['key']))
                            continue
                        complete(row,[evidence])
                    progress()
            unresolved=db.execute("SELECT count(*) FROM candidates WHERE state IN ('pending','fetching')").fetchone()[0]
            phase='interrupted' if STOP else 'deadline' if unresolved else 'complete'
        except (BudgetExhausted, APIUnavailable) as error:
            phase,reason='budget_reached' if isinstance(error,BudgetExhausted) else 'api_unavailable',str(error)
        finally:
            if 'legacy' in locals():legacy.db.close()
            snapshot(root,job,db,cache,phase,reason)
            cache.close();db.close()


def cleanup(root):
    """Remove only finished-job scratch; retain checkpoints and paid evidence."""
    removed=[]
    with (root/'run.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return None
        for folder in (root/'fetches').glob('*'):
            if folder.is_symlink() or not folder.is_dir():continue
            for name in ('seeds.txt','sources.json','stderr.log'):
                path=folder/name
                if path.is_file() and not path.is_symlink() and (name!='stderr.log' or path.stat().st_size==0):
                    path.unlink();removed.append(str(path))
        report={'observedAt':now_iso(),'removed':removed,
            'retained':['job.json','input.json','discovery-scope.json','candidates.sqlite','decisions.sqlite',
                'candidate-classifications.json','tutor-research-candidates.json','fetches/*/crawl.json',
                'fetches/*/audit.json','nonempty fetch error logs','source checkout awaiting PR review'],
            'note':'Reusable caches, full source checkpoints, budget reservations and final exports are preserved.'}
        atomic_json(root/'cleanup.json',report)
        return report


def watch(root, owner, monitor):
    job=json.loads((root/'job.json').read_text())
    status=json.loads((root/'status.json').read_text()) if (root/'status.json').exists() else {}
    process=subprocess_status(owner)
    terminal=status.get('phase') in ('complete','deadline','budget_reached','api_unavailable') or time.time()>=job['deadlineUnix']
    previous=json.loads((root/'monitor.json').read_text()) if (root/'monitor.json').exists() else {}
    recoveries=previous.get('recoveries',0)
    report={'observedAt':now_iso(),'processLive':process,'phase':status.get('phase'),'terminal':terminal,'status':status,'recoveries':recoveries}
    atomic_json(root/'monitor.json',report)
    if terminal and not process:
        cleanup(root)
        import subprocess
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{monitor}'],capture_output=True,timeout=10)
    elif not terminal and not process and recoveries<3:
        import subprocess
        subprocess.run(['launchctl','kickstart',f'gui/{os.getuid()}/{owner}'],capture_output=True,timeout=10)
        report['recoveries']+=1
        atomic_json(root/'monitor.json',report)


def subprocess_status(owner):
    import subprocess
    result=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{owner}'],capture_output=True,text=True,timeout=10)
    return result.returncode==0 and bool(re.search(r'pid = [1-9]\d*',result.stdout))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['run','watch'])
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--owner-label');parser.add_argument('--monitor-label')
    args=parser.parse_args()
    if args.command=='run':run(args.run_dir)
    else:watch(args.run_dir,args.owner_label,args.monitor_label)
