"""Compare alternative indexes within the original bank, deadline and shared cap."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

from discovery import (CHAINS, ENDPOINTS, PaidCache, atomic_json, file_hash, now_iso,
                       provider_request, request_key, website_key)
from directory_profiles import admitted_url
from focused_frontier import link_signals
from incremental_discovery import load_job, result_kind, wake_qualification
from keyword_expansion import upstream_results, canonical_url
from lead_scope import load_scope, matching_page_subjects, MUSIC
from qualification_feeder import directory_entry, local_directory_entry, public_seed
from qualification_seeds import publish
import spend_limit

INSTRUCTION = "If we can't find anything interesting from those. Explore other DataForSEO endpoints imho."
MAXIMUM_CENTS = 50
STOP = False


def stop(*_):
    global STOP
    STOP = True


def plan(towns):
    chosen = [next(t for t in towns if t['name'] == name)
              for name in ('High Wycombe', 'Aylesbury', 'Milton Keynes')]
    rows = []
    for lane in ('local_finder', 'bing'):
        for town in chosen:
            payload = {'keyword': '11 plus maths English tutor ' + town['name'],
                       'location_coordinate': town['coordinate'] + ',200',
                       'language_code': 'en', 'depth': 20}
            if lane == 'local_finder': payload['se_domain'] = 'google.co.uk'
            rows.append({'lane': lane, 'payload': payload, 'maximumCents': 3})
    for subject, place in [('tutor', 'Buckinghamshire'), ('tuition', 'Buckinghamshire'),
                           ('tutor', 'Milton Keynes'), ('11 plus', 'Bucks')]:
        rows.append({'lane': 'html_terms', 'payload': {
            'search_terms': [subject, place], 'mode': 'entry', 'limit': 30}, 'maximumCents': 8})
    return rows


def nominations(envelope, lane, towns, scope, seen):
    results = list(upstream_results(envelope))
    if lane == 'html_terms':
        for task in envelope.get('tasks', []):
            for result in task.get('result') or []:
                for item in result.get('items') or []:
                    url = canonical_url('https://' + item.get('domain', ''))
                    if url: results.append((url, {k: item[k] for k in ('title', 'description') if isinstance(item.get(k), str)}))
    accepted, held, owners = [], Counter(), set(seen)
    for url, context in results:
        url = public_seed(url)
        if not url: held['non_public_url'] += 1; continue
        owner = website_key(url)
        if owner in owners: held['known_owner'] += 1; continue
        text = url + ' ' + ' '.join(str(v) for v in context.values())
        if CHAINS.search(text): held['chain'] += 1; continue
        cue, local = link_signals(url, context, towns)
        subjects = matching_page_subjects(text, scope)
        if MUSIC.search(text) and not subjects: held['music_only_hint'] += 1; continue
        if cue < .9 and not subjects and lane != 'html_terms': held['no_subject_or_tutor_hint'] += 1; continue
        if local < .8 and lane != 'html_terms': held['no_local_hint'] += 1; continue
        host = urlsplit(url).hostname.removeprefix('www.')
        if directory_entry(url) and (not admitted_url(url) or not local_directory_entry(url)):
            held['generic_directory'] += 1; continue
        if len(accepted) >= 15: held['branch_limit'] += 1; continue
        name = (context.get('name') or context.get('title') or host).strip()[:240]
        accepted.append({'name': name, 'website': url, 'sourceUrl': url})
        owners.add(owner)
    return accepted, dict(held), len(results)


def known_owners(job):
    root = Path(job['qualificationRoot'])
    with sqlite3.connect((root / 'candidates.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        owners = {website_key(c.get('website') or c.get('sourceUrl'))
                  for row in db.execute('SELECT candidate FROM candidates') for c in [json.loads(row[0])]}
    qjob = json.loads((root / 'job.json').read_text())
    baseline = qjob['knownContacts']
    if file_hash(baseline['path']) != baseline['sha256']: raise ValueError('Known tutor baseline changed')
    for path in [Path(baseline['path']), root / 'exports/automated-qualification/accepted.json']:
        if path.exists(): owners.update(website_key(r.get('website')) for r in json.loads(path.read_text()))
    return owners


def run(root):
    global STOP
    signal.signal(signal.SIGTERM, stop)
    root = Path(root); cfg = json.loads((root / 'job.json').read_text())
    campaign = Path(cfg['campaignRoot']); job, deadline = load_job(campaign)
    rows = json.loads((root / 'plan.json').read_text())
    if (cfg['instruction'] != INSTRUCTION or cfg['maximumCents'] != MAXIMUM_CENTS
            or file_hash(root / 'plan.json') != cfg['planSha256']
            or rows != plan(job['towns']) or sum(x['maximumCents'] for x in rows) > MAXIMUM_CENTS):
        raise ValueError('Endpoint trial plan changed')
    bank = Path(job['recoveryBankRoot']); report = {'observedAt': now_iso(), 'phase': 'running', 'results': [],
        'maximumCents': MAXIMUM_CENTS, 'outreachEligible': False, 'deadline': job['authorizedDeadline']}
    with (bank / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cache = PaidCache(bank, 5000, 0)
        try:
            if spend_limit.policy(cache.db) is None: raise ValueError('Shared reduced budget must be installed')
            seen = known_owners(job); empty = Counter(); scope = load_scope(campaign)
            for row in rows:
                if STOP or time.time() >= deadline - 120: report['phase'] = 'deadline_or_stop'; break
                lane = row['lane']; key = request_key(lane, row['payload'])
                if empty[lane] >= 2: continue
                if cache.usage()['remainingCents'] < row['maximumCents']: report['phase'] = 'budget_reached'; break
                envelope = provider_request(cache, lane, row['payload'], maximum_cents=row['maximumCents'])
                receipt = cache.get(key)
                if receipt is None:
                    report['phase'] = 'budget_reached'
                    break
                kind = result_kind(receipt, envelope)
                candidates, held, total = nominations(envelope or {}, lane, [t['name'] for t in job['towns']], scope, seen)
                batch = None
                if kind == 'success' and candidates:
                    batch = publish(Path(job['qualificationRoot']), candidates, 'https://docs.dataforseo.com' + ENDPOINTS[lane])
                    seen.update(website_key(c['website']) for c in candidates)
                    wake_qualification(job)
                empty[lane] = 0 if candidates else empty[lane] + 1
                observation = {'key': key, 'lane': lane, 'outcome': kind, 'returnedUrls': total,
                    'newResearchNominations': len(candidates), 'heldReasons': held, 'batchSha256': batch,
                    'candidates': candidates, 'receiptSha256': receipt.get('receipt_hash') if receipt else None,
                    'reportedCostUsd': receipt.get('cost_usd') if receipt else None, 'observedAt': now_iso()}
                atomic_json(root / 'observations' / (key + '.json'), observation)
                report['results'].append(observation); report.update(observedAt=now_iso(), budget=cache.usage())
                atomic_json(root / 'status.json', report)
                if kind in ('uncertain', 'account_blocked'): report['phase'] = kind; break
            if report['phase'] == 'running': report['phase'] = 'complete'
        finally:
            report.update(observedAt=now_iso(), budget=cache.usage())
            atomic_json(root / 'status.json', report); cache.db.close()
    if report['phase'] in ('uncertain', 'account_blocked'):
        state = json.loads((campaign / 'runtime.json').read_text())
        state.update(phase='account_blocked' if report['phase'] == 'account_blocked' else 'api_unavailable',
                     observedAt=now_iso(), endpointProbeStop=str(root / 'status.json'))
        atomic_json(campaign / 'runtime.json', state)
        return
    # Resume the existing owner only with its unchanged authorization and deadline.
    if not STOP and time.time() < deadline - 120:
        subprocess.run(['launchctl', 'kickstart', f'gui/{os.getuid()}/com.jud.{campaign.name}'], capture_output=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--launch', action='store_true')
    args = parser.parse_args()
    if args.launch:
        cfg = json.loads((args.run_dir / 'job.json').read_text())
        _, deadline = load_job(Path(cfg['campaignRoot']))
        if time.time() >= deadline - 120: raise SystemExit(0)
        from launch import runner_environment
        os.execve(sys.executable, [sys.executable, __file__, '--run-dir', str(args.run_dir)], runner_environment(os.environ))
    run(args.run_dir)
