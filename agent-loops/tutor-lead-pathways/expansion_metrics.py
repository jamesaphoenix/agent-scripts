"""Observe paid tactic yield without conflating URLs with verified people."""
import argparse
import collections
import json
from pathlib import Path
from urllib.parse import urlsplit
from discovery import PaidCache, atomic_json, now_iso, public_url, website_key
from source_groups import tactic_group, positive_sites
from configuration import focused_roots
from lead_scope import load_scope, partition_contacts
from keyword_expansion import upstream_results


def result_urls(response):
    # Use the same public destinations as the upstream queue. Search controls,
    # ad redirects and tracking variations cannot earn a novelty reward.
    return {url for url, _ in upstream_results(response)}


def metrics(root, cache, plan):
    scope = load_scope(root)
    rows = cache.db.execute('SELECT * FROM requests ORDER BY created_at,key').fetchall()
    inherited = set()
    for row in rows:
        if row['imported'] and row['receipt']:
            inherited.update(result_urls(cache.receipt(row)))
    by_key = {r['key']: r for r in plan}
    runtime_path = root / 'runtime.json'
    if runtime_path.exists():
        state = json.loads(runtime_path.read_text())
        for link in state.get('supplementaryQueries', []) + state.get('businessSupplementaryQueries', []):
            parent = by_key.get(link.get('parentKey'))
            child = cache.get(link.get('key'))
            if parent and child and child['lane'] == parent.get('lane') and child['key'] not in by_key:
                by_key[child['key']] = dict(parent, key=child['key'], payload=json.loads(child['payload']))
    audited_sources = {}
    audited_directories = collections.defaultdict(set)
    qualified_sources = set()
    manifest_path = root / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for folder in focused_roots(manifest):
            path = Path(folder) / 'research-candidates.json'
            if not path.exists():
                continue
            for candidate in json.loads(path.read_text()):
                decision = candidate.get('classification', {})
                kind = decision.get('kind')
                if (kind == 'independent_tutor' or kind == 'directory' and candidate.get('profileKey')) and (
                        decision.get('niche', 0) >= .75 and decision.get('kindProbability', 0) >= .65
                        and decision.get('local', 0) >= .7 and decision.get('outside', 1) < .8):
                    qualified_sources.add(candidate['url'])
        profile_root = manifest.get('profileRunDir')
        review = Path(profile_root) / 'review/latest-expansion.json' if profile_root else None
        if review and review.exists():
            audit = json.loads(review.read_text())
            delta = Path(audit['outputDirectory']) / 'this-stage-delta/new.json'
            if delta.exists():
                people = json.loads(delta.read_text())
                if scope:
                    people, _, _ = partition_contacts(people, scope)
                for person in people:
                    for url in person.get('sourceUrls', []):
                        audited_sources[website_key(url)] = person['canonicalId']
                        valid = public_url(url)
                        if valid:
                            site = urlsplit(valid).hostname.lower().removeprefix('www.')
                            audited_directories[site].add(person['canonicalId'])
    accepted = collections.defaultdict(set)
    qualified = collections.defaultdict(set)
    source_sites = collections.defaultdict(set)
    totals = collections.defaultdict(lambda: {'requests': 0, 'successfulRequests': 0, 'failedRequests': 0,
        'uncertainRequests': 0, 'emptySuccessfulRequests': 0, 'newResultUrls': 0, 'chargedCents': 0})
    observed = set(inherited)
    for row in rows:
        if row['imported']:
            continue
        tactic = tactic_group(by_key.get(row['key'], {'lane': row['lane']}))
        source_sites[tactic].update(positive_sites(json.loads(row['payload']).get('keyword', '')))
        item = totals[tactic]
        item['requests'] += 1
        item['chargedCents'] += row['cost_cents']
        urls = result_urls(cache.receipt(row)) if row['receipt'] else set()
        qualified[tactic].update((urls - inherited) & qualified_sources)
        accepted[tactic].update(audited_sources[key] for url in urls if (key := website_key(url)) in audited_sources)
        item['newResultUrls'] += len(urls - observed)
        observed.update(urls)
        if row['status'] == 'succeeded':
            item['successfulRequests'] += 1
            item['emptySuccessfulRequests'] += int(not urls)
        elif row['status'] == 'failed':
            item['failedRequests'] += 1
        else:
            item['uncertainRequests'] += 1
    for tactic, item in totals.items():
        settled = item['successfulRequests'] + item['failedRequests']
        item['successRate'] = item['successfulRequests'] / settled if settled else None
        item['newUrlsPerRequest'] = item['newResultUrls'] / item['requests']
        item['usdPerNewUrl'] = item['chargedCents'] / 100 / item['newResultUrls'] if item['newResultUrls'] else None
        item['auditedNewIdentitiesAttributed'] = len(accepted[tactic])
        # Recursive profiles establish directory yield even when the original
        # paid result was an index. Keep that separate from direct attribution.
        item['sourceSites'] = sorted(source_sites[tactic])
        item['auditedSourceIdentities'] = len(set().union(*(audited_directories[site] for site in source_sites[tactic])))
        item['classifiedLocalTutorCandidates'] = len(qualified[tactic])
        item['usdPerAttributedAuditedIdentity'] = item['chargedCents'] / 100 / len(accepted[tactic]) if accepted[tactic] else None
    return {'observedAt': now_iso(), 'tactics': dict(totals), 'budget': cache.usage(),
            'inheritedResultUrls': len(inherited), 'newResultUrls': len(observed - inherited),
            'urlCountsAreVerifiedTutors': False}


def write_metrics(root, cache, plan):
    value = metrics(root, cache, plan)
    atomic_json(root / 'metrics/latest.json', value)
    path = root / 'metrics/history.jsonl'
    with path.open('a') as stream:
        stream.write(json.dumps(value) + '\n')
    path.chmod(0o600)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    root = parser.parse_args().run_dir
    job = json.loads((root / 'manifest.json').read_text())
    cache = PaidCache(root, job['budgetCents'], job['priorExposureCents'])
    try:
        print(json.dumps(write_metrics(root, cache, json.loads((root / 'plan.json').read_text()))))
    finally:
        cache.db.close()
