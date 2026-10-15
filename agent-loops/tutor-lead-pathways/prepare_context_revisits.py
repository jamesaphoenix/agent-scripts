"""Prepare an offline context proposal. Never fetch pages or submit decisions."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from candidate_review import page_index
from configuration import loaded_source_sha
from decisions_classifier import decision_request
from discovery import atomic_json, file_hash, now_iso, source_host, website_key
from focused_frontier import admitted, site_identity
from lead_scope import load_scope
from qualification_context import cache_path, select_urls, select_pages
from qualify_inventory import input_evidence, owned_questions, VERSION
from urllib.parse import urlsplit


def prepare(root):
    root = Path(root).resolve()
    job = json.loads((root / 'job.json').read_text())
    scope = load_scope(root)
    index = page_index(list(map(Path, job['frontiers'])), list(map(Path, job['crawlRoots'])),
                       [root, *map(Path, job.get('fetchRoots', []))])
    proposals, skipped = [], Counter()
    with closing(sqlite3.connect((root / 'candidates.sqlite').as_uri() + '?mode=ro', uri=True)) as db, \
            closing(sqlite3.connect(cache_path(root, job).as_uri() + '?mode=ro', uri=True)) as cache:
        db.row_factory = cache.row_factory = sqlite3.Row
        claims = {r['url']: r['state'] for r in db.execute('SELECT url,state FROM fetch_claims')}
        site_counts = Counter(site_identity(url) for url in claims)
        for row in db.execute("SELECT * FROM candidates WHERE state IN "
                              "('uncertain','subject_review','locality_review','needs_owned_page_review') "
                              "AND decision IS NOT NULL ORDER BY key"):
            candidate, decision = json.loads(row['candidate']), json.loads(row['decision'])
            url = admitted(candidate.get('website') or candidate.get('sourceUrl'))
            if (not url or source_host(urlsplit(url).hostname) or decision.get('kind') != 'independent_tutor'
                    or decision.get('kindProbability', 0) < .8 or decision.get('outside', 1) >= .8
                    or (decision.get('niche', 0) < .5 and decision.get('local', 0) < .85)):
                continue
            prior = cache.execute('SELECT state FROM decision_requests WHERE key=?', (decision['decisionKey'],)).fetchone()
            if prior is None or prior['state'] != 'succeeded':
                skipped['prior_decision_not_settled'] += 1
                continue
            old = json.loads(row['evidence'] or '[]')
            old_urls = {p['url'] for p in old}
            texts = {p.get('text') for p in old}
            selected = select_urls(candidate, index)
            targets, missing, blocked = [], [], False
            for target in selected:
                page = index.get(target)
                if page and page.get('replaySource') and website_key(page['url']) == website_key(url):
                    if page['text'] not in texts:
                        targets.append({'url': target, 'state': 'cached_context',
                                        'textSha256': hashlib.sha256(page['text'].encode()).hexdigest(),
                                        'sourceProof': page['replaySource']})
                    continue
                parents = [index[u] for u in old_urls if u in index and index[u].get('replaySource')
                           and any(admitted(link.get('url')) == target
                           for link in index[u].get('links', [])[:100])]
                if (target in claims or site_counts[site_identity(target)] >= 8 or not parents
                        or website_key(target) != website_key(url)):
                    blocked = True
                    break
                missing.append(target)
                targets.append({'url': target, 'state': 'unfetched_observed_link',
                                'sourceUrl': parents[0]['url'], 'sourceProof': parents[0]['replaySource']})
            if blocked:
                skipped['selected_context_held_or_unproven'] += 1
                continue
            if not targets:
                continue
            request_state, request_key = 'needs_context_fetch', None
            if not missing:
                _, request_key, _ = decision_request(input_evidence(candidate,
                    select_pages(candidate, index), scope), owned_questions(job['towns'], scope), VERSION)
                previous = cache.execute('SELECT state FROM decision_requests WHERE key=?', (request_key,)).fetchone()
                if previous and previous['state'] != 'succeeded':
                    skipped['new_input_already_held'] += 1
                    continue
                request_state = 'already_settled' if previous else 'new_input'
            proposals.append({'candidateId': row['key'], 'name': candidate.get('name'),
                'website': url, 'currentState': row['state'], 'priorDecisionKey': decision['decisionKey'],
                'selectedUrls': selected, 'targets': targets, 'classificationState': request_state,
                'newDecisionKey': request_key, 'outreachEligible': False})
    report = {'observedAt': now_iso(), 'sourceSha': loaded_source_sha(), 'state': 'awaiting_window_authorization',
        'dispatchEnabled': False, 'paidCalls': 0, 'sourceJobSha256': file_hash(root / 'job.json'),
        'maximumHours': 12, 'candidateCount': len(proposals),
        'targetTypes': dict(Counter(t['state'] for p in proposals for t in p['targets'])),
        'classificationStates': dict(Counter(p['classificationState'] for p in proposals)),
        'skippedReasons': dict(skipped), 'candidates': proposals}
    atomic_json(root / 'verification/owned-context-revisits-proposal.json', report)
    return {k: v for k, v in report.items() if k != 'candidates'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    print(json.dumps(prepare(parser.parse_args().run_dir)))
