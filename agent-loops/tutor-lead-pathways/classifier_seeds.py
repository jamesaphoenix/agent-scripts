"""Pass fresh search URLs and saved Go text to the classifier's single writer."""
import hashlib
import json
from pathlib import Path

from discovery import atomic_json, file_hash, now_iso
from expansion_metrics import result_urls
from focused_frontier import SITE_IDENTITY_VERSION, admitted

MAX_BATCH_BYTES = 8 * 1024 * 1024


def publish(root, destinations, cache):
    entries = {}
    for row in cache.db.execute("SELECT * FROM requests WHERE imported=0 AND status='succeeded' AND receipt IS NOT NULL ORDER BY key"):
        payload = json.loads(row['payload'])
        for candidate in sorted(result_urls(cache.receipt(row))):
            url = admitted(candidate)
            if url:
                entries.setdefault(url, {'url': url, 'context': {'query': payload.get('keyword', '')},
                    'group': row['key'], 'provenance': {'requestKey': row['key'], 'receiptSha256': row['receipt_hash']}})
    for path in sorted((root / 'sdk/results').glob('slot-*.json')):
        value = json.loads(path.read_text())
        for record in value.get('leads', []):
            for candidate in [record.get('website'), *record.get('sourceUrls', [])]:
                url = admitted(candidate)
                if url:
                    entries.setdefault(url, {'url': url, 'context': {'title': record.get('name', ''),
                        'localityClaim': record.get('localityClaim', '')}, 'group': 'sdk-public-research',
                        'provenance': {'sdkResult': str(path), 'resultSha256': file_hash(path)}})
    reused = 0
    for path in sorted((root / 'crawls').glob('*/crawl.json')):
        # The owner may still be writing its current checkpoint. Atomic writes
        # make each read a complete snapshot; subsequent batches add later rows.
        raw = path.read_bytes()
        saved = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        for result in saved.get('results', []):
            evidence = result.get('evidence') or {}
            url = admitted(result.get('node', {}).get('url'))
            text = evidence.get('text')
            if result.get('error') or not url or not isinstance(text, str) or not 80 <= len(text) <= 50000:
                continue
            entry = entries.setdefault(url, {'url': url, 'context': {'title': evidence.get('title') or ''},
                'group': 'go-crawl-reuse', 'provenance': {}})
            entry['evidence'] = dict(evidence, textSha256=hashlib.sha256(text.encode()).hexdigest())
            entry['provenance']['crawlCheckpoint'] = str(path)
            entry['provenance']['crawlCheckpointSha256'] = digest
            reused += 1
    batches = []
    rows = [entries[url] for url in sorted(entries)]
    for offset in range(0, len(rows), 100):
        batch = {'siteIdentityVersion': SITE_IDENTITY_VERSION, 'entries': rows[offset:offset + 100]}
        raw = (json.dumps(batch, indent=2, ensure_ascii=False) + '\n').encode()
        if len(raw) > MAX_BATCH_BYTES:
            raise ValueError('Classifier seed batch exceeds its bound')
        digest = hashlib.sha256(raw).hexdigest()
        for destination in destinations:
            if not destination.exists():
                continue
            path = destination / 'seed-batches' / (digest + '.json')
            if not path.exists():
                atomic_json(path, batch)
            if file_hash(path) != digest:
                raise ValueError('Classifier seed batch changed')
        batches.append(digest)
    report = {'observedAt': now_iso(), 'urls': len(entries), 'cachedEvidencePages': reused,
              'batches': batches, 'paidCalls': 0, 'frontierWrites': 0}
    atomic_json(root / 'classifier-seeds/latest.json', report)
    return report


def ingest(root, frontier):
    """Only the owner calls this, preserving the frontier's single writer."""
    status_path = root / 'seed-ingestion.json'
    status = json.loads(status_path.read_text()) if status_path.exists() else {'processed': {}, 'admittedUrls': 0}
    added = 0
    for path in sorted((root / 'seed-batches').glob('*.json')):
        if path.name in status['processed']:
            continue
        try:
            if path.is_symlink() or path.stat().st_size > MAX_BATCH_BYTES or file_hash(path) != path.stem:
                raise ValueError('Classifier seed batch failed integrity validation')
            value = json.loads(path.read_text())
            rows = value['entries']
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError('Invalid classifier seed batch')
            # Validate the whole batch before inserting any of its records.
            for row in rows:
                if (not isinstance(row, dict) or not admitted(row.get('url')) or not isinstance(row.get('context'), dict)
                        or not isinstance(row.get('group'), str) or not isinstance(row.get('provenance'), dict)):
                    raise ValueError('Invalid classifier seed URL or context')
                evidence = row.get('evidence')
                if evidence is not None:
                    text = evidence.get('text') if isinstance(evidence, dict) else None
                    if (not isinstance(text, str) or not 80 <= len(text) <= 50000
                            or hashlib.sha256(text.encode()).hexdigest() != evidence.get('textSha256')):
                        raise ValueError('Classifier seed evidence hash mismatch')
            count = frontier.add_many(rows)
            added += count
            status['admittedUrls'] += count
            status['processed'][path.name] = {'status': 'ingested', 'rows': len(rows), 'added': count}
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Bad input is held individually. It must never stop other batches.
            status['processed'][path.name] = {'status': 'held', 'reason': type(error).__name__}
        status['observedAt'] = now_iso()
        atomic_json(status_path, status)
    return added
