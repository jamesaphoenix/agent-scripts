"""Import observed public website seeds into the existing qualification owner."""
import hashlib
import json
from pathlib import Path

from discovery import atomic_json, now_iso, website_key
from focused_frontier import admitted
from qualification_context import PRIVATE_ROUTE
from urllib.parse import urlsplit


def publish(root, candidates, source_url):
    value = {'schemaVersion': 1, 'sourceUrl': source_url, 'candidates': candidates}
    body = (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()
    key = hashlib.sha256(body).hexdigest()
    path = Path(root) / 'website-seeds' / (key + '.json')
    if not path.exists():
        atomic_json(path, value)
    return key


def import_batches(root, db):
    from qualify_inventory import candidate_key
    db.execute('''CREATE TABLE IF NOT EXISTS website_seed_batches (
        key TEXT PRIMARY KEY, state TEXT NOT NULL, imported INTEGER NOT NULL,
        skipped INTEGER NOT NULL, error TEXT, observed_at TEXT NOT NULL)''')
    db.commit()
    existing = {website_key(c.get('website') or c.get('sourceUrl'))
                for row in db.execute('SELECT candidate FROM candidates')
                for c in [json.loads(row['candidate'])]}
    for path in sorted((Path(root) / 'website-seeds').glob('*.json')):
        if path.is_symlink() or db.execute('SELECT 1 FROM website_seed_batches WHERE key=?', (path.stem,)).fetchone():
            continue
        added = skipped = 0
        error = None
        try:
            if path.stat().st_size > 128000:
                raise ValueError('Website seed batch exceeded 128 KB')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != path.stem:
                raise ValueError('Website seed batch hash mismatch')
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'sourceUrl', 'candidates'}
                    or value['schemaVersion'] != 1 or not admitted(value['sourceUrl'])
                    or not isinstance(value['candidates'], list) or not 1 <= len(value['candidates']) <= 100):
                raise ValueError('Invalid observed website seed batch')
            prepared = []
            for item in value['candidates']:
                if not isinstance(item, dict) or set(item) - {'name', 'website', 'sourceUrl', 'siteEvidence'}:
                    raise ValueError('Only public website seed fields are allowed')
                name, url = item.get('name'), admitted(item.get('website'))
                source = admitted(item.get('sourceUrl') or url)
                owner = website_key(url)
                if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 240 or not url or not source
                        or website_key(source) != owner or PRIVATE_ROUTE.search(urlsplit(url).path)):
                    raise ValueError('Invalid public website seed owner')
                observed = item.get('siteEvidence', [])
                if (not isinstance(observed, list) or len(observed) > 10 or any(
                        not isinstance(p, dict) or set(p) != {'url'} or not admitted(p['url'])
                        or website_key(p['url']) != owner or PRIVATE_ROUTE.search(urlsplit(p['url']).path)
                        for p in observed)):
                    raise ValueError('Seed context must contain observed public pages of the same owner')
                candidate = {'name': name.strip(), 'website': url, 'sourceUrl': source, 'siteEvidence': observed,
                    'status': 'research', 'identifiers': ['website-seed:' + owner],
                    'provenance': [{'lane': 'web_seed', 'sourceUrl': value['sourceUrl'], 'batchSha256': path.stem}],
                    'outreachEligible': False}
                prepared.append((owner, candidate))
            # Validate the whole batch before changing the queue. Existing
            # owners retain their settled decisions and failed-fetch holds.
            with db:
                total = db.execute('SELECT coalesce(sum(imported),0) FROM website_seed_batches').fetchone()[0]
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='qualification_feed'").fetchone():
                    total += db.execute("SELECT count(*) FROM qualification_feed WHERE state IN ('admitted','delegated_source')").fetchone()[0]
                for owner, candidate in prepared:
                    if owner in existing or total + added >= 2000:
                        skipped += 1
                        continue
                    db.execute('INSERT INTO candidates(key,candidate) VALUES(?,?)',
                               (candidate_key(candidate), json.dumps(candidate)))
                    existing.add(owner); added += 1
                db.execute('INSERT INTO website_seed_batches VALUES(?,?,?,?,?,?)',
                           (path.stem, 'imported', added, skipped, None, now_iso()))
        except (OSError, ValueError, KeyError, TypeError) as cause:
            error = type(cause).__name__ + ': ' + str(cause)
            with db:
                db.execute('INSERT OR IGNORE INTO website_seed_batches VALUES(?,?,?,?,?,?)',
                           (path.stem, 'held', 0, 0, error, now_iso()))
    return {'importedCandidates': db.execute('SELECT coalesce(sum(imported),0) FROM website_seed_batches').fetchone()[0],
            'heldBatches': db.execute("SELECT count(*) FROM website_seed_batches WHERE state='held'").fetchone()[0]}
