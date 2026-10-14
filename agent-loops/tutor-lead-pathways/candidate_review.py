#!/usr/bin/env python3
"""Triage an inventory with saved Decisions responses, without network calls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from decisions_classifier import MODEL, PROMPT_VERSION, NANO_PER_INPUT_TOKEN, decision_request, decision_text, parse_decision, questions
from discovery import atomic_json, now_iso, source_host, website_key
from focused_frontier import admitted, profile_key
from lead_scope import matching_page_subjects, load_scope
from urllib.parse import urlsplit


class CachedDecisions:
    """Read-only replay. A missing or uncertain request cannot trigger a paid call."""
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        try:
            config = json.loads(self.db.execute('SELECT value FROM decision_config WHERE id=1').fetchone()[0])
            if config['model'] != MODEL or config['promptVersion'] != PROMPT_VERSION:
                raise ValueError('Unsupported saved decision contract')
            self.towns = config['towns']
            self.questions = questions(sorted(self.towns))
        except Exception:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def lookup(self, evidence):
        payload, key, _ = decision_request(evidence, self.questions)
        row = self.db.execute('SELECT * FROM decision_requests WHERE key=?', (key,)).fetchone()
        if row is None:
            return None
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if row['state'] != 'succeeded' or row['payload_sha256'] != digest:
            raise ValueError('Saved decision requires reconciliation')
        reply = json.loads(row['response'])
        tokens = reply.get('usage', {}).get('input_tokens')
        if type(tokens) is not int or tokens < 1 or row['spent_nano'] != tokens * NANO_PER_INPUT_TOKEN:
            raise ValueError('Saved decision usage proof changed')
        return dict(parse_decision(reply), decisionKey=key, cacheHit=True)


def primary_urls(row):
    return sorted({url for field in ('website', 'sourceUrl') if (url := admitted(row.get(field)))})


def candidate_urls(row):
    """Include observed owned pages while keeping directory and tenant boundaries."""
    urls = set(primary_urls(row))
    website = admitted(row.get('website'))
    if not website:
        return sorted(urls)
    host = urlsplit(website).hostname.removeprefix('www.')
    if source_host(host) or host in ('google.com', 'sites.google.com') or profile_key(website):
        return sorted(urls)
    owner = website_key(website)
    for entry in row.get('siteEvidence', [])[:20]:
        url = admitted(entry.get('url')) if isinstance(entry, dict) else None
        if url and owner and website_key(url) == owner:
            urls.add(url)
    return sorted(urls)


def assess(row, pages, towns, known_urls, scope=None):
    result = {'name': row.get('name', ''), 'website': row.get('website'), 'sourceUrl': row.get('sourceUrl'),
              'outreachEligible': False, 'qualifiedContact': False, 'subjectHints': [],
              'evidence': [{'url': p['evidence']['url'], 'decisionKey': p['decision']['decisionKey'],
                            'kind': p['decision']['kind'],
                            'evidenceSha256': hashlib.sha256(json.dumps(p['evidence'], sort_keys=True).encode()).hexdigest()}
                           for p in pages]}
    if set(primary_urls(row)) & known_urls:
        return dict(result, status='already_in_tutor_export')
    if not pages:
        return dict(result, status='needs_api_decision')
    kinds = {p['decision']['kind'] for p in pages}
    if len(kinds) != 1:
        return dict(result, status='conflicting_decisions')
    kind = next(iter(kinds))
    strong = [p for p in pages if p['decision']['kindProbability'] >= .65]
    if not strong or kind == 'uncertain':
        return dict(result, status='model_review')
    if kind in ('institution', 'irrelevant'):
        return dict(result, status='excluded_' + kind)
    if kind == 'directory':
        return dict(result, status='profile_review' if any(profile_key(p['evidence']['url']) for p in pages)
                    else 'source_to_expand')
    if all(p['decision']['outside'] >= .8 for p in strong):
        return dict(result, status='outside_area')
    local = [p for p in strong if p['decision']['niche'] >= .75 and p['decision']['local'] >= .7
             and p['decision']['outside'] < .8 and any(re.search(r'\b' + re.escape(town) + r'\b',
                 decision_text(p['evidence']), re.I) for town in [*towns, 'Buckinghamshire', 'Bucks', 'Milton Keynes'])]
    if not local:
        return dict(result, status='locality_review')
    scope = scope or {'subjects': ['english', 'maths', 'verbal_reasoning', 'non_verbal_reasoning'],
                     'includeElevenPlus': True, 'includePrimaryTutors': True}
    hints = sorted({subject for p in local for subject in matching_page_subjects(
        decision_text(p['evidence']), scope)})
    return dict(result, subjectHints=hints, status='tutor_candidate' if hints else 'subject_review')


def page_index(frontiers, crawl_roots=(), fetch_roots=()):
    pages = {}
    def observe(url, evidence, proof):
        final = admitted(evidence.get('url'))
        if (not url or not final or not isinstance(evidence.get('text'), str)
                or not 80 <= len(evidence['text']) <= 50000):
            return
        # Replay the observed final URL, preserving its exact decision contract.
        # The redirect can connect owned pages but cannot connect other profiles
        # or other tenants on a shared host.
        if url != final and website_key(url) != website_key(final):
            return
        observed = dict(evidence, replaySource=proof)
        pages[url] = observed
        pages[final] = observed
    checkpoints = [path for root in crawl_roots for path in (root / 'crawls').glob('*/crawl.json')]
    checkpoints += [path for root in fetch_roots for path in (root / 'fetches').glob('*/crawl.json')]
    checkpoints += [path for root in fetch_roots for path in (root / 'fetches').glob('*/audit.json')]
    for path in sorted(checkpoints):
        try:
            if path.is_symlink() or path.stat().st_size > 20 * 1024 * 1024:
                continue
            raw = path.read_bytes()
            checkpoint = json.loads(raw)
            digest = hashlib.sha256(raw).hexdigest()
            rows = checkpoint.get('results', [])
            if path.name == 'audit.json':
                rows = [{'node': {'url': row.get('url')}, 'error': row.get('error'),
                         'evidence': {k: row.get(k) for k in ('url', 'title', 'text', 'links')}}
                        for row in checkpoint.get('pages', [])]
            for row in rows:
                evidence = row.get('evidence') or {}
                url = admitted(row.get('node', {}).get('url'))
                if not row.get('error'):
                    observe(url, evidence, {'checkpoint': str(path), 'sha256': digest})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    for path in frontiers:
        db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            for row in db.execute('SELECT url,evidence FROM pages WHERE evidence IS NOT NULL ORDER BY key'):
                url = admitted(row['url'])
                try:
                    evidence = json.loads(row['evidence'])
                    observe(url, evidence, {'frontier': str(path),
                        'evidenceSha256': hashlib.sha256(row['evidence'].encode()).hexdigest()})
                except (ValueError, TypeError, AttributeError):
                    continue
        finally:
            db.close()
    return pages


def run(candidates, frontiers, cache_path, contacts, output, scope=None, crawl_roots=()):
    raw = candidates.read_bytes()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot = output / 'input-candidates.json'
    snapshot.write_bytes(raw)
    snapshot.chmod(0o600)
    rows = [r for r in json.loads(raw) if r.get('status') == 'research' and not r.get('sourceOnly')]
    known = {url for path in contacts for row in json.loads(path.read_text())
             for source in row.get('sourceUrls', []) if (url := admitted(source))}
    index = page_index(frontiers, crawl_roots)
    results, requests = [], {}
    with CachedDecisions(cache_path) as cache:
        for row in rows:
            record_key = hashlib.sha256(json.dumps([row.get('name'), row.get('website'), row.get('sourceUrl'),
                sorted(row.get('identifiers', []))], sort_keys=True).encode()).hexdigest()
            pages, missing, held = [], [], []
            already_known = bool(set(primary_urls(row)) & known)
            for url in candidate_urls(row):
                evidence = index.get(url)
                if evidence is None:
                    continue
                try:
                    decision = cache.lookup(evidence)
                    if decision:
                        pages.append({'evidence': evidence, 'decision': decision})
                    elif not already_known:
                        payload, key, reserve = decision_request(evidence, cache.questions)
                        missing.append(key)
                        request = requests.setdefault(key, {'requestKey': key, 'maximumReservationUsd': reserve / 1e9,
                            'evidence': {k: evidence.get(k, '') for k in ('url', 'title', 'description', 'text')},
                            'candidateIds': [], 'payloadSha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()})
                        request['candidateIds'].append(record_key)
                except (ValueError, TypeError, KeyError):
                    held.append(url)
            result = assess(row, pages, cache.towns, known, scope)
            if held and result['status'] != 'already_in_tutor_export':
                result['status'] = 'held_decision'
            if result['status'] == 'needs_api_decision' and not missing:
                result['status'] = 'needs_page_context'
            results.append(dict(result, candidateId=record_key, missingDecisionKeys=missing, heldUrls=held))
    statuses = Counter(r['status'] for r in results)
    atomic_json(output / 'candidate-assessments.json', results)
    atomic_json(output / 'tutor-candidates.json', [r for r in results if r['status'] == 'tutor_candidate'])
    atomic_json(output / 'pending-decisions.json', list(requests.values()))
    report = {'observedAt': now_iso(), 'inputSha256': hashlib.sha256(raw).hexdigest(), 'candidateRows': len(rows),
              'statuses': dict(statuses), 'rowsWithReplayedDecisions': sum(bool(r['evidence']) for r in results),
              'uniquePendingDecisions': len(requests), 'pendingMaximumReservationUsd': sum(r['maximumReservationUsd'] for r in requests.values()),
              'paidCalls': 0, 'outreachEligible': 0, 'newQualifiedContacts': 0,
              'qualification': 'Cached model triage only. Positive candidates require owned service, locality, identity and contact-route review before tutor export.'}
    atomic_json(output / 'summary.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--frontier', type=Path, action='append', required=True)
    parser.add_argument('--decisions-cache', type=Path, required=True)
    parser.add_argument('--contacts', type=Path, action='append', default=[])
    parser.add_argument('--crawl-root', type=Path, action='append', default=[])
    parser.add_argument('--scope-run-dir', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.candidates, args.frontier, args.decisions_cache, args.contacts, args.output_dir,
                         load_scope(args.scope_run_dir) if args.scope_run_dir else None, args.crawl_root)))
