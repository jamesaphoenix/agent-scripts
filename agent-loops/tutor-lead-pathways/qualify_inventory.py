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
from discovery import atomic_json, file_hash, now_iso, website_key
from focused_frontier import admitted, site_identity
from focused_run import fetch
from lead_scope import load_scope, scope_prompt, matching_page_subjects, matching_subjects
from configuration import loaded_source_sha
from qualification_context import cache_path, context_urls, select_urls, select_pages, fallback_pages, profile_context_index
from directory_profiles import admitted_url, exclusion_reason, profile_locality_status
from qualification_exports import publish as publish_structured_profiles
from qualification_seeds import import_batches as import_website_seeds, pending_batches
from qualification_feeder import refill as refill_qualification, summary as feed_summary
from owned_context_refresh import refresh as refresh_owned_contexts

VERSION = 'bucks-owned-candidate-v2'
STOP = False


def candidate_key(candidate):
    return hashlib.sha256(json.dumps([candidate.get('name'),candidate.get('website'),candidate.get('sourceUrl'),
        sorted(candidate.get('identifiers', []))],sort_keys=True).encode()).hexdigest()


def publish_profile_seeds(job, urls):
    if not job.get('profileRoots') or not urls:
        return None
    urls = sorted({u for url in urls if (u := admitted_url(url))})[:5000]
    value = {'urls':urls,'priorityUrls':urls[:100],
             'qualification':'Context-fetch queue only. Own profile, subjects and locality still require verification.'}
    raw = (json.dumps(value,indent=2)+'\n').encode()
    key = hashlib.sha256(raw).hexdigest()
    path = Path(job['profileRoots'][0])/'seed-batches'/(key+'.json')
    if not path.exists():atomic_json(path,value)
    return key


def import_profile_candidates(db, job, scope):
    """Import verified local public profiles without borrowing directory headings."""
    known = set(job.get('knownContactUrls', []))
    existing = {url for row in db.execute('SELECT candidate FROM candidates')
                for url in candidate_urls(json.loads(row['candidate']))}
    for folder in job.get('profileRoots', []):
        path = Path(folder)/'bucks-private-tutors.json'
        if not path.exists() or path.stat().st_size > 32*1024*1024:
            continue
        for profile in json.loads(path.read_text()):
            urls = profile.get('sourceUrls', [])
            if not urls or not matching_subjects(profile, scope) or profile.get('institution') or profile.get('locationConflict'):
                continue
            url = urls[0]
            if url in known or url in existing:
                continue
            candidate = {'name':profile['name'],'website':url,'sourceUrl':url,'status':'research',
                         'description':'Public profile fields require owned context and identity review.',
                         'identifiers':['public-profile:'+url],'aliases':[profile['name']]}
            if url not in profile_context_index([Path(folder)], [candidate]):
                continue
            with db:
                db.execute('INSERT OR IGNORE INTO candidates(key,candidate) VALUES(?,?)',
                           (candidate_key(candidate),json.dumps(candidate)))
            existing.add(url)


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


def input_evidence(candidate, pages, scope=None, subject_policy_version=2):
    metadata = {k: candidate.get(k) for k in ('name', 'aliases', 'website', 'sourceUrl', 'advertisedAddress', 'category', 'description')}
    metadata['aliases'] = (metadata['aliases'] or [])[:10]
    observations = [{'url': p['url'], 'title': p.get('title'), 'text': p.get('text', '')[:4000]}
                    for p in pages[:3]]
    value = {'candidate': metadata, 'ownedPageObservations': observations}
    if scope:
        value['requestedSubjectPolicy'] = {
            'subjects': scope['subjects'],
            'englishIncludes': ['literacy', 'phonics', 'reading', 'spelling', 'creative writing'] if 'english' in scope['subjects'] else [],
            'mathsIncludes': ['numeracy', 'arithmetic', 'algebra', 'geometry', 'dyscalculia'] if 'maths' in scope['subjects'] else [],
            'includeElevenPlus': scope['includeElevenPlus'],
            'includePrimaryTutors': scope['includePrimaryTutors'],
            'evidenceRule': 'Require own advertised tuition. Qualifications, assessments alone, past jobs, navigation and neighbouring adverts cannot establish an offer.'}
        if subject_policy_version == 2:
            value['requestedSubjectPolicy']['selectionRule'] = (
                'ANY ONE requested subject, explicit eleven-plus tuition or primary tuition is sufficient. '
                'The candidate does not need to teach all requested subjects.')
            value['requestedSubjectPolicy']['geographyRule'] = (
                'Evaluate the niche subject predicate independently of geography. '
                'Evaluate advertised locality only in the separate local and outside predicates.')
    text = json.dumps(value, ensure_ascii=False)
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
    if any(re.search(r'\bapply\s+to\s+franchise\b', p.get('text', ''), re.I) for p in pages):
        return 'franchise_review'
    return 'tutor_research_candidate'


def recheck_saved_outcomes(db, scope):
    """Reapply local disposition rules to settled decisions without API requests."""
    states = ('uncertain', 'excluded_institution', 'excluded_irrelevant', 'source_to_expand',
              'outside_area', 'subject_review', 'locality_review', 'needs_owned_page_review',
              'tutor_research_candidate', 'franchise_review')
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


def refresh_verified_profile_contexts(db, cache, index, scope):
    """Reassess changed owned fields, preserving the original paid receipts."""
    changed = 0
    with db:
        for row in db.execute("SELECT key,candidate,evidence,decision FROM candidates WHERE decision IS NOT NULL AND state NOT IN ('pending','fetching','decision_held','fetch_held')").fetchall():
            candidate = json.loads(row['candidate'])
            old = {p['url']: p for p in json.loads(row['evidence'] or '[]')}
            replacements = [index[u] for u in candidate_urls(candidate) if u in index
                            and index[u].get('extractionKind') == 'public_profile_fields_v2'
                            and index[u].get('replaySource') and index[u].get('url') == u
                            and old.get(u, {}).get('text') != index[u]['text']]
            if len(replacements) != 1:
                continue
            fields = json.loads(replacements[0]['text'])['publicProfileFields']
            if (fields.get('locationConflict') or exclusion_reason(fields)
                    or profile_locality_status(fields) != 'advertised_bucks_locality'
                    or not matching_subjects(fields, scope)):
                continue
            decision = json.loads(row['decision'])
            receipt = cache.db.execute('SELECT state FROM decision_requests WHERE key=?', (decision.get('decisionKey'),)).fetchone()
            if receipt is None or receipt['state'] != 'succeeded':
                continue
            db.execute("UPDATE candidates SET state='pending',error='Verified own profile fields replace generic page context',updated_at=CURRENT_TIMESTAMP WHERE key=?", (row['key'],))
            changed += 1
    return changed


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
        'refreshedProfileContexts': job.get('refreshedProfileContexts', 0),
        'refreshedOwnedContexts': job.get('refreshedOwnedContexts', 0),
        'qualification': 'Model-assessed research candidates. Owned identities, contact routes and duplicate matching remain required before audited contact export.'}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='website_seed_batches'").fetchone():
        status['websiteSeeds'] = {'importedCandidates': db.execute(
            'SELECT coalesce(sum(imported),0) FROM website_seed_batches').fetchone()[0],
            'skippedExistingOwners': db.execute('SELECT coalesce(sum(skipped),0) FROM website_seed_batches').fetchone()[0],
            'heldBatches': db.execute("SELECT count(*) FROM website_seed_batches WHERE state='held'").fetchone()[0]}
    feed = feed_summary(db)
    if feed:
        status['continuousSeeding'] = feed
    if job.get('feedError'):
        status['continuousSeedingError'] = job['feedError']
    if job.get('publicRosterQualification'):
        status['publicRosterQualification'] = job['publicRosterQualification']
    if job.get('publicRosterError'):
        status['publicRosterError'] = job['publicRosterError']
    atomic_json(root / 'status.json', status)
    return status


def run(root):
    global STOP
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    job = json.loads((root / 'job.json').read_text())
    if job.get('windowAuthorization') and file_hash(Path(job['windowAuthorization'])) != job['windowAuthorizationSha256']:
        raise ValueError('The authorised context window changed')
    for key, pathkey in [('inputSha256', 'input'), ('scopeSha256', 'scope'), ('crawlerSha256', 'crawler')]:
        if file_hash(Path(job[pathkey])) != job[key]:
            raise ValueError('Immutable classification input changed: ' + pathkey)
    job['loadedSourceSha'] = loaded_source_sha()
    scope = load_scope(Path(job['scope']).parent)
    ledger_path = cache_path(root, job)
    with (root / 'run.lock').open('a') as lock, ledger_path.with_suffix('.owner.lock').open('a') as ledger_lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(ledger_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        db = create_database(root / 'candidates.sqlite')
        cache = DecisionsCache(ledger_path, job['towns'], cap_nano=job['authorization']['additionalCapCents'] * 10_000_000,
            prior_exposure_cents=10000, additional_authorization=job['authorization'],
            question_set=owned_questions(job['towns'], scope), prompt_version=VERSION)
        phase,reason='running',None
        last_snapshot=0
        try:
            if job.get('expandOwnedContext') and (root / 'public-rosters').exists():
                from public_rosters import qualify_cached_rosters
                baseline = []
                frozen = job.get('knownContacts')
                if frozen:
                    if file_hash(Path(frozen['path'])) != frozen['sha256']:
                        raise ValueError('Frozen tutor baseline changed')
                    baseline.extend(json.loads(Path(frozen['path']).read_text()))
                for path in [root / 'exports/audited-subject-contacts.json', root / 'exports/automated-qualification/accepted.json']:
                    if path.exists():
                        baseline.extend(json.loads(path.read_text()))
                try:
                    job['publicRosterQualification'] = qualify_cached_rosters(root, job, db, cache, scope, baseline, lambda: STOP)
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                    job['publicRosterError'] = type(error).__name__ + ': ' + str(error)
            with db:
                db.execute("UPDATE candidates SET state='fetch_held',error='Previous fetch interrupted; not replayed' WHERE state='fetching'")
                for candidate in json.loads(Path(job['input']).read_text()):
                    if candidate.get('status') != 'research' or candidate.get('sourceOnly'):
                        continue
                    key = candidate_key(candidate)
                    db.execute('INSERT OR IGNORE INTO candidates(key,candidate) VALUES(?,?)',(key,json.dumps(candidate)))
            import_profile_candidates(db, job, scope)
            if job.get('expandOwnedContext'):
                import_website_seeds(root, db)
            recheck_saved_outcomes(db, scope)
            index = page_index([Path(p) for p in job['frontiers']], [Path(p) for p in job['crawlRoots']],
                               [root, *[Path(p) for p in job.get('fetchRoots', [])]])
            candidates = [json.loads(r['candidate']) for r in db.execute('SELECT candidate FROM candidates')]
            index.update(profile_context_index([Path(p) for p in job.get('profileRoots', [])], candidates))
            if job.get('expandOwnedContext'):
                job['refreshedProfileContexts'] = refresh_verified_profile_contexts(db, cache, index, scope)
                job['refreshedOwnedContexts'] = refresh_owned_contexts(db, cache, index, scope, job)
            if job.get('expandOwnedContext'):
                try:
                    refill_qualification(root, job, db, scope, index)
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                    # A damaged feeder source must not stop other candidates.
                    job['feedError'] = type(error).__name__ + ': ' + str(error)
            # A verified saved observation can resolve a failed fetch without
            # retrying that request or resetting any paid submission.
            with db:
                for row in db.execute("SELECT key,candidate FROM candidates WHERE state IN ('fetch_held','awaiting_profile_context') AND decision IS NULL AND (evidence IS NULL OR evidence='[]')").fetchall():
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
                    decision = cache.classify(input_evidence(json.loads(row['candidate']), pages,
                        scope if job.get('expandOwnedContext') else None))
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
                        db.execute("UPDATE candidates SET state='decision_held',decision=NULL,evidence=?,error=? WHERE key=?",
                            (json.dumps(pages),str(error),row['key']))
                    api_failures += 1
                    if api_failures >= 5:
                        raise APIUnavailable('Five consecutive held decisions; reconciliation required')
            pending_fetches = []
            delegated_profiles = []
            base_pages = {}
            def observed_pages(candidate):
                return select_pages(candidate, index) if job.get('expandOwnedContext') else [index[u] for u in candidate_urls(candidate) if u in index][:3]
            def missing_context(candidate):
                urls = select_urls(candidate, index) if job.get('expandOwnedContext') else candidate_urls(candidate)[:1]
                return [u for u in urls if u not in index and u not in blocked_urls and site_identity(u) != 'google.com']
            for row in db.execute("SELECT * FROM candidates WHERE state='pending' ORDER BY key").fetchall():
                if STOP or time.time() >= job['deadlineUnix']:
                    break
                candidate = json.loads(row['candidate'])
                pages = observed_pages(candidate)
                held=False
                for page in pages:
                    try:legacy.lookup(page)
                    except (ValueError,KeyError,TypeError):held=True
                if held:
                    with db:db.execute("UPDATE candidates SET state='decision_held',error='Legacy decision requires reconciliation' WHERE key=?",(row['key'],))
                    continue
                missing = missing_context(candidate)
                if pages and not missing:
                    complete(row,pages)
                else:
                    urls = missing or [u for u in candidate_urls(candidate) if site_identity(u) != 'google.com']
                    if not urls:
                        complete(row, [])
                    elif urls[0] in blocked_urls:
                        with db:db.execute("UPDATE candidates SET state='fetch_held',error='Previously blocked or failed public source; not retried' WHERE key=?",(row['key'],))
                    else:
                        if job.get('profileRoots') and admitted_url(urls[0]):
                            delegated_profiles.append(urls[0])
                            with db:db.execute("UPDATE candidates SET state='awaiting_profile_context',decision=NULL,evidence=NULL,error='Delegated to the registered public-profile worker' WHERE key=?",(row['key'],))
                            continue
                        base_pages[row['key']] = (fallback_pages(candidate, json.loads(row['evidence'] or '[]'), index)
                                                  if job.get('expandOwnedContext') else pages)
                        pending_fetches.append((row,urls[0]))
                progress()
            publish_profile_seeds(job,delegated_profiles)
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
                                index[url] = json.loads(previous['evidence'])
                                complete(row,observed_pages(json.loads(row['candidate'])))
                            else:
                                if base_pages.get(row['key']):complete(row,base_pages[row['key']])
                                else:
                                    with db: db.execute("UPDATE candidates SET state='fetch_held',error='Previous fetch held' WHERE key=?",(row['key'],))
                            continue
                        if site_count[site] >= 8:
                            if base_pages.get(row['key']):complete(row,base_pages[row['key']])
                            else:
                                with db: db.execute("UPDATE candidates SET state='fetch_held',error='Eight-page site allowance reached' WHERE key=?",(row['key'],))
                            continue
                        site_count[site]+=1;next_site[site]=time.time()+60
                        with db:
                            db.execute("INSERT INTO fetch_claims VALUES(?,'submitting',NULL,NULL,?)",(url,time.time()))
                            db.execute("UPDATE candidates SET state='fetching' WHERE key=?",(row['key'],))
                        fetch_row={'url':url,'key':hashlib.sha256(url.encode()).hexdigest() if job.get('expandOwnedContext') else row['key'],'site':site,'evidence':None}
                        inflight[pool.submit(fetch,job,root,fetch_row)]=(row,url)
                    if not inflight:
                        time.sleep(1)
                        continue
                    done,_=wait(inflight,timeout=1,return_when=FIRST_COMPLETED)
                    for future in done:
                        row,url=inflight.pop(future)
                        try:
                            evidence=future.result()
                            if job.get('expandOwnedContext') and website_key(url) != website_key(evidence.get('url')):
                                raise ValueError('Fetched context left its original owner')
                            with db: db.execute("UPDATE fetch_claims SET state='succeeded',evidence=? WHERE url=?",(json.dumps(evidence),url))
                        except Exception as error:
                            with db:
                                db.execute("UPDATE fetch_claims SET state='held',error=? WHERE url=?",(type(error).__name__,url))
                                if not base_pages.get(row['key']):
                                    db.execute("UPDATE candidates SET state='fetch_held',error=? WHERE key=?",(type(error).__name__,row['key']))
                            if base_pages.get(row['key']):complete(row,base_pages[row['key']])
                            continue
                        index[url] = evidence
                        candidate = json.loads(row['candidate'])
                        pages = observed_pages(candidate)
                        missing = missing_context(candidate)
                        if job.get('expandOwnedContext') and missing:
                            base_pages[row['key']] = fallback_pages(candidate, json.loads(row['evidence'] or '[]'), index)
                            pending_fetches.append((row,missing[0]))
                            with db:db.execute("UPDATE candidates SET state='pending',evidence=? WHERE key=?",(json.dumps(pages),row['key']))
                        else:
                            complete(row,pages)
                    progress()
            unresolved=db.execute("SELECT count(*) FROM candidates WHERE state IN ('pending','fetching')").fetchone()[0]
            phase='interrupted' if STOP else 'deadline' if unresolved else 'complete'
        except (BudgetExhausted, APIUnavailable) as error:
            phase,reason='budget_reached' if isinstance(error,BudgetExhausted) else 'api_unavailable',str(error)
        finally:
            if 'legacy' in locals():legacy.db.close()
            try:
                automated = None
                publication_error = None
                if phase == 'complete' and job.get('knownContacts'):
                    try:
                        publication_index = page_index([Path(p) for p in job['frontiers']],
                            [Path(p) for p in job['crawlRoots']], [root, *[Path(p) for p in job.get('fetchRoots', [])]])
                        publication_index.update(profile_context_index([Path(p) for p in job.get('profileRoots', [])],
                            [json.loads(r['candidate']) for r in db.execute('SELECT candidate FROM candidates')]))
                        automated = publish_structured_profiles(root, job, db, scope, publication_index,
                                                               owned_questions(job['towns'], scope))
                    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                        publication_error = type(error).__name__ + ': ' + str(error)
                status = snapshot(root,job,db,cache,phase,reason)
                if automated:
                    status['automatedQualification'] = automated
                if publication_error:
                    status['automatedQualificationError'] = publication_error
                if automated or publication_error:
                    atomic_json(root / 'status.json', status)
            finally:
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
            'retained':['job.json','input.json','discovery-scope.json','candidates.sqlite',
                str(cache_path(root,json.loads((root/'job.json').read_text()))) if (root/'job.json').exists() else 'decisions.sqlite',
                'candidate-classifications.json','tutor-research-candidates.json','fetches/*/crawl.json',
                'fetches/*/audit.json','nonempty fetch error logs','source checkout awaiting PR review'],
            'note':'Reusable caches, full source checkpoints, budget reservations and final exports are preserved.'}
        atomic_json(root/'cleanup.json',report)
        return report


def resident(root, poll_seconds=120):
    """Keep one in-memory API credential while new source batches arrive."""
    job = json.loads((root / 'job.json').read_text())
    while not STOP and time.time() < job['deadlineUnix']:
        run(root)
        status = json.loads((root / 'status.json').read_text())
        if STOP or status.get('phase') in ('deadline', 'budget_reached', 'api_unavailable'):
            return
        until = min(time.time() + poll_seconds, job['deadlineUnix'])
        while not STOP and time.time() < until:
            time.sleep(min(1, until - time.time()))


def watch(root, owner, monitor):
    job=json.loads((root/'job.json').read_text())
    status=json.loads((root/'status.json').read_text()) if (root/'status.json').exists() else {}
    process=subprocess_status(owner)
    producer_live = bool(job.get('refreshContext') and job.get('contextOwnerLabel') and subprocess_status(job['contextOwnerLabel']))
    feeder_ready = bool(status.get('continuousSeeding', {}).get('lastRefill', {}).get('canRefill'))
    continuous_window = bool(job.get('expandOwnedContext'))
    seed_batches, seed_error = 0, None
    if continuous_window:
        try:
            seed_batches = pending_batches(root)
        except (OSError, ValueError, sqlite3.Error) as error:
            seed_error = type(error).__name__ + ': ' + str(error)
    sources_ready = bool(producer_live or feeder_ready or seed_batches)
    terminal = (status.get('phase') in ('deadline', 'budget_reached', 'api_unavailable')
                or time.time() >= job['deadlineUnix']
                or (status.get('phase') == 'complete' and not (continuous_window or sources_ready)))
    previous=json.loads((root/'monitor.json').read_text()) if (root/'monitor.json').exists() else {}
    recoveries=previous.get('recoveries',0)
    report={'observedAt':now_iso(),'processLive':process,'contextProducerLive':producer_live,'feederReady':feeder_ready,'phase':status.get('phase'),'terminal':terminal,'status':status,'recoveries':recoveries,
            'pendingWebsiteSeedBatches':seed_batches,'seedIntakeError':seed_error}
    atomic_json(root/'monitor.json',report)
    from launch import credential_delay
    delay = credential_delay(root) if not terminal and not process else 0
    if delay:
        report['action'] = 'credential_cooldown'
        atomic_json(root/'monitor.json', report)
        # A thirty-minute launchd tick may precede a known account-quota
        # reset by a few minutes. Finish only that bounded wait here so the
        # owner need not lose another full interval. Longer waits remain
        # launchd's responsibility.
        if delay > 300 or time.time() + delay + 1 >= job['deadlineUnix']:
            return
        time.sleep(delay + 1)
        if (time.time() >= job['deadlineUnix'] or credential_delay(root)
                or subprocess_status(owner)):
            return
        latest = json.loads((root/'status.json').read_text()) if (root/'status.json').exists() else {}
        if latest.get('phase') in ('deadline', 'budget_reached', 'api_unavailable'):
            return
        import subprocess
        subprocess.run(['launchctl','kickstart',f'gui/{os.getuid()}/{owner}'],capture_output=True,timeout=10)
        report['action'] = 'credential_retry_after_cooldown'
        atomic_json(root/'monitor.json', report)
        return
    if terminal and not process:
        cleanup(root)
        import subprocess
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{monitor}'],capture_output=True,timeout=10)
    elif not terminal and not process and status.get('phase')=='complete':
        if sources_ready:
            import subprocess
            subprocess.run(['launchctl','kickstart',f'gui/{os.getuid()}/{owner}'],capture_output=True,timeout=10)
            report['action']='refresh_saved_context'
        else:
            # Keep the scheduled check alive until its original deadline. New
            # source batches can arrive after both current queues have drained.
            report['action']='await_new_sources'
        atomic_json(root/'monitor.json',report)
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
    parser.add_argument('command',choices=['run','resident','watch'])
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--owner-label');parser.add_argument('--monitor-label')
    args=parser.parse_args()
    if args.command=='run':run(args.run_dir)
    elif args.command=='resident':resident(args.run_dir)
    else:watch(args.run_dir,args.owner_label,args.monitor_label)
