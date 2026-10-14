"""Refill the qualification owner from observed sources, without buying searches.

Search hints nominate a page, never establish a tutor's identity or locality.
Only the existing owner writes candidates, lineage and admission receipts.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

from directory_profiles import admitted_url, decode_page, profile_record, profile_locality_status
from discovery import file_hash, now_iso, professional_profile, request_key, source_host, website_key
from focused_frontier import admitted, link_signals, profile_key, TUTOR
from keyword_expansion import search_identity, upstream_results
from lead_scope import matching_page_subjects, matching_subjects
from qualification_context import PRIVATE_ROUTE

LOW_WATER = 40
TARGET = 80
MAX_OWNERS = 2000
MAX_BRANCH = 15
MAX_ROOT = 30
MAX_HOPS = 2
ACTIVE = ('pending', 'fetching')
POSITIVE = 'tutor_research_candidate'


def tables(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS qualification_feed (
        key TEXT PRIMARY KEY, url TEXT NOT NULL, source_group TEXT NOT NULL,
        parent TEXT NOT NULL, root TEXT NOT NULL, hops INTEGER NOT NULL,
        state TEXT NOT NULL, candidate_key TEXT, proof TEXT, error TEXT,
        observed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS qualification_feed_runs (
        id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, summary TEXT NOT NULL);''')


def summary(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='qualification_feed'").fetchone():
        return None
    counts = dict(db.execute('SELECT state,count(*) FROM qualification_feed GROUP BY state'))
    yields = [dict(r) for r in db.execute('''SELECT f.source_group AS source,
        count(*) AS seeded, sum(c.state='tutor_research_candidate') AS subjectLocalMatches,
        sum(c.decision IS NOT NULL) AS classified
        FROM qualification_feed f JOIN candidates c ON c.key=f.candidate_key
        WHERE f.state='admitted' GROUP BY f.source_group ORDER BY subjectLocalMatches DESC''')]
    last = db.execute('SELECT summary FROM qualification_feed_runs ORDER BY id DESC LIMIT 1').fetchone()
    return {'counts': counts, 'sourceYield': yields, 'lastRefill': json.loads(last[0]) if last else None}


def public_seed(url):
    url = admitted(url)
    if not url or PRIVATE_ROUTE.search(urlsplit(url).path):
        return None
    if urlsplit(url).hostname.endswith(('.au', '.us', '.ca', '.in', '.nz', '.za')):
        return None
    return url


def directory_entry(url):
    return source_host(urlsplit(url).hostname.removeprefix('www.')) and not (profile_key(url) or professional_profile(url))


def search_offers(path, towns, seen):
    if not path or not Path(path).is_file() or Path(path).is_symlink():
        return []
    offers = []
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as index:
        index.row_factory = sqlite3.Row
        rows = index.execute('''SELECT u.url,u.context,s.*,o.receipt_path,o.receipt_hash,o.task_id,
            q.query_key,q.town,q.lane,q.payload FROM upstream_urls u
            JOIN url_sources s ON s.url_key=u.url_key
            JOIN search_observations o ON o.run_dir=s.run_dir AND o.request_key=s.request_key
            JOIN search_queries q ON q.query_key=o.query_key
            WHERE u.state IN ('pending','classified') AND o.receipt_verified=1 AND o.status='succeeded'
            ORDER BY u.url_key,s.request_key''')
        for row in rows:
            url = public_seed(row['url'])
            if not url or website_key(url) in seen or row['town'].casefold() not in {t.casefold() for t in towns}:
                continue
            try:
                context = json.loads(row['context'])
                if not isinstance(context, dict):
                    continue
            except (ValueError, TypeError):
                continue
            cue, local = link_signals(url, context, towns)
            if cue < .9:
                continue
            directory = directory_entry(url)
            if directory and (local < .8 or not admitted_url(url)):
                continue
            offers.append({'url': url, 'name': context.get('name') or context.get('title') or urlsplit(url).hostname,
                'group': 'search:' + row['lane'] + ':' + row['town'], 'parent': 'search:' + row['request_key'],
                'root': 'search:' + row['query_key'], 'hops': 0, 'score': cue + local + (0 if directory else 1),
                'directory': directory,
                'search': dict(row), 'proof': {'receipt': row['receipt_path'], 'sha256': row['receipt_hash']}})
    return offers


def verify_search(offer, receipts):
    row = offer['search']
    key = row['receipt_path'], row['receipt_hash']
    if key not in receipts:
        path = Path(row['receipt_path'])
        if path.is_symlink() or path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Invalid search receipt file')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['receipt_hash'] or row['receipt_sha256'] != row['receipt_hash']:
            raise ValueError('Search receipt hash changed')
        saved = json.loads(raw)
        envelope = saved['response']
        tasks = envelope['tasks']
        if (saved['key'] != row['request_key'] or request_key(saved['lane'], saved['payload']) != saved['key']
                or search_identity(saved['lane'], saved['payload']) != row['query_key']
                or envelope.get('status_code') != 20000 or len(tasks) != 1
                or tasks[0]['id'] != row['task_id'] or tasks[0]['status_code'] not in (20000, 40106)):
            raise ValueError('Search receipt attribution changed')
        receipts[key] = dict(upstream_results(envelope))
    if offer['url'] not in receipts[key]:
        raise ValueError('Seed URL is absent from its search receipt')
    actual = receipts[key][offer['url']]
    offer['name'] = actual.get('name') or actual.get('title') or urlsplit(offer['url']).hostname


def profile_offers(job, scope, seen):
    offers = []
    for folder in job.get('profileRoots', []):
        root = Path(folder)
        path = root / 'bucks-private-tutors.json'
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
            continue
        for hint in json.loads(path.read_text()):
            url = public_seed(hint.get('website'))
            if not url or website_key(url) in seen or not hint.get('sourceUrls'):
                continue
            source = hint['sourceUrls'][0]
            key = hashlib.sha256(source.encode()).hexdigest()
            try:
                receipt_path, body_path = root / 'receipts' / (key + '.json'), root / 'pages' / (key + '.html')
                if (receipt_path.is_symlink() or body_path.is_symlink() or receipt_path.stat().st_size > 64000
                        or body_path.stat().st_size > 8 * 1024 * 1024):
                    continue
                receipt = json.loads(receipt_path.read_text())
                body = body_path.read_bytes()
                if (receipt.get('url') != source or receipt.get('status') != 200 or receipt.get('error')
                        or hashlib.sha256(body).hexdigest() != receipt.get('sha256')):
                    continue
                profile = profile_record(decode_page(body, receipt.get('contentType', '')), source)
                if (not profile or profile.get('sourceUrls') != [source] or profile.get('personId') != hint.get('personId')
                        or public_seed(profile.get('website')) != url or profile.get('institution')
                        or not matching_subjects(profile, scope) or profile_locality_status(profile) != 'advertised_bucks_locality'):
                    continue
                offers.append({'url': url, 'name': profile['name'], 'group': 'profile:' + profile['platform'],
                    'parent': source, 'root': source, 'hops': 1, 'score': 2.9,
                    'proof': {'receipt': str(receipt_path), 'sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                              'bodySha256': receipt['sha256']}})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return offers


def link_offers(db, index, scope, towns, seen):
    offers = []
    for row in db.execute("SELECT key,candidate,state,evidence FROM candidates WHERE state IN ('tutor_research_candidate','source_to_expand')"):
        candidate = json.loads(row['candidate'])
        lineage = db.execute("SELECT root,hops FROM qualification_feed WHERE candidate_key=? AND state='admitted'", (row['key'],)).fetchone()
        root, hops = (lineage['root'], lineage['hops'] + 1) if lineage else (row['key'], 1)
        if hops > MAX_HOPS:
            continue
        for stored in json.loads(row['evidence'] or '[]'):
            page = index.get(stored['url'])
            if not page or page.get('text') != stored.get('text') or not page.get('replaySource'):
                continue
            # A directory can be a useful bridge even though its own offer is
            # not an individual tutor's. Children still need their own proof.
            if row['state'] == 'source_to_expand' and (not matching_page_subjects(page['text'], scope)
                    or not any(re.search(r'\b' + re.escape(t) + r'\b', page['text'], re.I) for t in towns)):
                continue
            for link in page.get('links', [])[:500]:
                url = public_seed(link.get('url'))
                anchor = link.get('anchor', '')
                if (not url or website_key(url) in seen or website_key(url) == website_key(page['url'])
                        or not TUTOR.search(url + ' ' + anchor)):
                    continue
                cue, local = link_signals(url, {'anchor': anchor}, towns)
                directory = directory_entry(url)
                if directory and local < .8:
                    continue
                if directory and (not admitted_url(url) or not matching_page_subjects(url + ' ' + anchor, scope)):
                    continue
                offers.append({'url': url, 'name': anchor.strip() or urlsplit(url).hostname,
                    'group': 'links:' + website_key(page['url']), 'parent': row['key'], 'root': root,
                    'hops': hops, 'score': cue + local + (0 if directory else 1), 'directory': directory,
                    'proof': dict(page['replaySource'], sourceUrl=page['url'])})
    return offers


def refill(root, job, db, scope, index):
    from qualify_inventory import candidate_key
    tables(db)
    active = db.execute('SELECT count(*) FROM candidates WHERE state IN (?,?)', ACTIVE).fetchone()[0]
    if time.time() >= job['deadlineUnix'] or active >= LOW_WATER:
        return summary(db)
    seen = {website_key(c.get('website') or c.get('sourceUrl'))
            for r in db.execute('SELECT candidate FROM candidates') for c in [json.loads(r['candidate'])]}
    seen.update(website_key(u) for u in job.get('blockedUrls', []))
    seen.update(website_key(u) for u in job.get('knownContactUrls', []))
    if job.get('knownContacts'):
        baseline = job['knownContacts']
        if file_hash(Path(baseline['path'])) != baseline['sha256']:
            raise ValueError('Known contact snapshot changed')
        seen.update(website_key(r.get('website')) for r in json.loads(Path(baseline['path']).read_text()))
    path = job.get('keywordIndexPath')
    if not path and job.get('discoveryCampaign'):
        path = json.loads((Path(job['discoveryCampaign']) / 'job.json').read_text()).get('keywordIndexPath')
    offers, source_errors = [], []
    for name, read in [('search', lambda: search_offers(path, job['towns'], seen)),
                       ('profiles', lambda: profile_offers(job, scope, seen)),
                       ('links', lambda: link_offers(db, index, scope, job['towns'], seen))]:
        try:
            offers.extend(read())
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            source_errors.append({'source': name, 'error': type(error).__name__ + ': ' + str(error)})
    yields = {r['source']: (r['subjectLocalMatches'] + 1) / (r['classified'] + 2) for r in summary(db)['sourceYield']}
    # A URL appearing in many searches is one offer, not many spare seeds.
    ordered = sorted(offers, key=lambda o: (-(o['score'] + yields.get(o['group'], .5)), o['url']))
    ranked = list({o['url']: o for o in reversed(ordered)}.values())
    ranked.sort(key=lambda o: (-(o['score'] + yields.get(o['group'], .5)), o['url']))
    receipts, added, skipped, held, delegated = {}, 0, 0, 0, []
    prior = db.execute("SELECT count(*) FROM qualification_feed WHERE state IN ('admitted','delegated_source')").fetchone()[0]
    manual = db.execute('SELECT coalesce(sum(imported),0) FROM website_seed_batches').fetchone()[0]
    while ranked and active + added < TARGET and prior + manual + added + len(delegated) < MAX_OWNERS and time.time() < job['deadlineUnix']:
        # Keep a small exploration slot, still within subject and locality gates.
        position = -1 if (prior + added + 1) % 10 == 0 else 0
        offer = ranked.pop(position)
        owner = website_key(offer['url'])
        key = hashlib.sha256(json.dumps([offer['url'], offer['parent'], offer['proof']], sort_keys=True).encode()).hexdigest()
        if owner in seen or db.execute('SELECT 1 FROM qualification_feed WHERE key=?', (key,)).fetchone():
            skipped += 1
            continue
        branch = db.execute("SELECT count(*) FROM qualification_feed WHERE state IN ('admitted','delegated_source') AND parent=?", (offer['parent'],)).fetchone()[0]
        roots = db.execute("SELECT count(*) FROM qualification_feed WHERE state IN ('admitted','delegated_source') AND root=?", (offer['root'],)).fetchone()[0]
        if branch >= MAX_BRANCH or roots >= MAX_ROOT:
            skipped += 1
            continue
        state, error, ckey = 'admitted', None, None
        try:
            if 'search' in offer:
                verify_search(offer, receipts)
            if offer.get('directory'):
                # Registered profile workers own these indexes. Do not ask an
                # individual-tutor question about every subject index.
                if (len(delegated) >= 10 or not job.get('profileRoots')
                        or not matching_page_subjects(offer['url'] + ' ' + offer['name'], scope)):
                    skipped += 1
                    continue
                from qualify_inventory import publish_profile_seeds
                publish_profile_seeds(job, [offer['url']])
                with db:
                    db.execute('INSERT INTO qualification_feed VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (key, offer['url'], offer['group'], offer['parent'], offer['root'], offer['hops'],
                         'delegated_source', None, json.dumps(offer['proof']), None, now_iso()))
                delegated.append(offer['url']); seen.add(owner)
                continue
            candidate = {'name': offer['name'].strip()[:240], 'website': offer['url'], 'sourceUrl': offer['url'],
                'status': 'research', 'identifiers': ['continuous-seed:' + owner],
                'provenance': [{'lane': 'continuous_seed', 'source': offer['group'], 'parent': offer['parent'],
                                'root': offer['root'], 'hops': offer['hops'], 'proof': offer['proof']}],
                'outreachEligible': False}
            ckey = candidate_key(candidate)
            with db:
                db.execute('INSERT INTO candidates(key,candidate) VALUES(?,?)', (ckey, json.dumps(candidate)))
                db.execute('INSERT INTO qualification_feed VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (key, offer['url'], offer['group'], offer['parent'], offer['root'], offer['hops'], state,
                     ckey, json.dumps(offer['proof']), None, now_iso()))
            seen.add(owner); added += 1
        except (OSError, ValueError, KeyError, TypeError) as cause:
            error = type(cause).__name__ + ': ' + str(cause)
            with db:
                db.execute('INSERT INTO qualification_feed VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (key, offer['url'], offer['group'], offer['parent'], offer['root'], offer['hops'], 'held',
                     None, json.dumps(offer['proof']), error, now_iso()))
            held += 1
    result = {'added': added, 'skipped': skipped, 'held': held, 'remainingObservedOffers': len(ranked),
              'lowWater': LOW_WATER, 'target': TARGET, 'maximumNewOwners': MAX_OWNERS, 'paidSearchCalls': 0,
              'canRefill': prior + manual + added + len(delegated) < MAX_OWNERS and bool(added or delegated or ranked),
              'delegatedDirectoryEntrypoints': delegated, 'sourceErrors': source_errors}
    with db:
        db.execute('INSERT INTO qualification_feed_runs(observed_at,summary) VALUES(?,?)', (now_iso(), json.dumps(result)))
    return summary(db)
