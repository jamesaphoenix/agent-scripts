"""Bridge verified provider receipts into the bounded public-profile crawler."""
import argparse
import hashlib
import json
from pathlib import Path
from directory_profiles import admitted_url
from discovery import PaidCache, atomic_json, now_iso
from expansion_metrics import result_urls
from configuration import focused_roots


def write_seed_batch(root, destination, cache):
    urls, origins = set(), []
    for row in cache.db.execute("SELECT * FROM requests WHERE status='succeeded' AND receipt IS NOT NULL"):
        selected = sorted({url for candidate in result_urls(cache.receipt(row))
                           if (url := admitted_url(candidate))})
        if selected:
            urls.update(selected)
            origins.append({'requestKey': row['key'], 'receiptSha256': row['receipt_hash'], 'urls': selected})
    for path in sorted((root / 'sdk/results').glob('slot-*.json')):
        value = json.loads(path.read_text())
        selected = sorted({url for row in value.get('leads', [])
                           for candidate in [row.get('website'), *row.get('sourceUrls', [])]
                           if (url := admitted_url(candidate))})
        if selected:
            urls.update(selected)
            origins.append({'sdkResult': str(path), 'resultSha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'urls': selected})
    manifest = json.loads((root / 'manifest.json').read_text()) if (root / 'manifest.json').exists() else {}
    for folder in focused_roots(manifest):
        path = Path(folder) / 'profile-candidates-not-in-baseline.json'
        if path.exists():
            selected = sorted({url for row in json.loads(path.read_text()) if (url := admitted_url(row.get('url')))})
            urls.update(selected)
            origins.append({'focusedExport': str(path), 'exportSha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'urls': selected})
    # Public URL hashes and attribution make re-import idempotent. The profile
    # stage owns seen/frontier/page counters and never trusts search locality.
    all_urls = sorted(urls)
    batches = []
    for offset in range(0, len(all_urls), 5000):
        chunk = all_urls[offset:offset + 5000]
        raw = (json.dumps({'urls': chunk}, indent=2) + '\n').encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = destination / 'seed-batches' / (digest + '.json')
        if not path.exists():
            atomic_json(path, {'urls': chunk})
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Seed batch changed')
        batches.append(digest)
    summary = {'observedAt': now_iso(), 'urls': len(urls), 'batches': batches,
               'destination': str(destination), 'receiptAttributions': origins}
    atomic_json(root / 'profile-seeds/latest.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--profile-run-dir', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.run_dir / 'manifest.json').read_text())
    cache = PaidCache(args.run_dir, manifest['budgetCents'], manifest['priorExposureCents'])
    try:
        value = write_seed_batch(args.run_dir, args.profile_run_dir, cache)
        print(json.dumps({k: v for k, v in value.items() if k != 'receiptAttributions'}))
    finally:
        cache.db.close()
