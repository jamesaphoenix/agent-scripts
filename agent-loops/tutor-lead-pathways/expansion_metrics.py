"""Observe paid tactic yield without conflating URLs with verified people."""
import argparse
import collections
import json
from pathlib import Path
from discovery import PaidCache, atomic_json, now_iso, public_url


def result_urls(response):
    urls = set()
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('url', 'website') and isinstance(item, str):
                    url = public_url(item)
                    if url:
                        urls.add(url)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for task in response.get('tasks', []):
        visit(task.get('result') or [])
    return urls


def metrics(root, cache, plan):
    rows = cache.db.execute('SELECT * FROM requests ORDER BY created_at,key').fetchall()
    inherited = set()
    for row in rows:
        if row['imported'] and row['receipt']:
            inherited.update(result_urls(cache.receipt(row)))
    by_key = {r['key']: r for r in plan}
    totals = collections.defaultdict(lambda: {'requests': 0, 'successfulRequests': 0, 'failedRequests': 0,
        'uncertainRequests': 0, 'emptySuccessfulRequests': 0, 'newResultUrls': 0, 'chargedCents': 0})
    observed = set(inherited)
    for row in rows:
        if row['imported']:
            continue
        tactic = by_key.get(row['key'], {}).get('tactic', row['lane'])
        item = totals[tactic]
        item['requests'] += 1
        item['chargedCents'] += row['cost_cents']
        urls = result_urls(cache.receipt(row)) if row['receipt'] else set()
        item['newResultUrls'] += len(urls - observed)
        observed.update(urls)
        if row['status'] == 'succeeded':
            item['successfulRequests'] += 1
            item['emptySuccessfulRequests'] += int(not urls)
        elif row['status'] == 'failed':
            item['failedRequests'] += 1
        else:
            item['uncertainRequests'] += 1
    for item in totals.values():
        settled = item['successfulRequests'] + item['failedRequests']
        item['successRate'] = item['successfulRequests'] / settled if settled else None
        item['newUrlsPerRequest'] = item['newResultUrls'] / item['requests']
        item['usdPerNewUrl'] = item['chargedCents'] / 100 / item['newResultUrls'] if item['newResultUrls'] else None
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
