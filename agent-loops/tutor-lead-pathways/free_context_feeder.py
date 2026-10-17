#!/usr/bin/env python3
"""Fetch queued public owned-site context without a classification credential."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from discovery import atomic_json, file_hash, now_iso, source_host, website_key
from focused_frontier import admitted, profile_key
from focused_run import fetch
from candidate_review import page_index
from qualification_context import PRIVATE_ROUTE
from urllib.parse import urlsplit

DEADLINE = "2026-10-18T09:46:36Z"
STAGE = "tutor-qualification-20261017-tutor-graph-16h"


def validate_url(value, job):
    url = admitted(value)
    if not url or url != value or PRIVATE_ROUTE.search(urlsplit(url).path):
        return None
    host = urlsplit(url).hostname.removeprefix('www.')
    if source_host(host) or profile_key(url) or host in {'facebook.com', 'instagram.com', 'sites.google.com'}:
        return None
    def hold_identity(candidate):
        parsed = urlsplit(candidate)
        return (parsed.hostname.removeprefix('www.') if parsed.hostname else None,
                parsed.path.rstrip('/') or '/', parsed.query)
    if any(hold_identity(url) == hold_identity(held) for held in job['blockedUrls']):
        return None
    return url


def open_ledger(path):
    db = sqlite3.connect(path)
    db.execute('''CREATE TABLE IF NOT EXISTS pages (
        url TEXT PRIMARY KEY, owner TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        state TEXT NOT NULL, observed_at TEXT NOT NULL, text_sha256 TEXT,
        checkpoint_sha256 TEXT, error TEXT, replay_proof TEXT)''')
    if 'replay_proof' not in {row[1] for row in db.execute('PRAGMA table_info(pages)')}:
        db.execute('ALTER TABLE pages ADD COLUMN replay_proof TEXT')
    db.commit()
    return db


def admit_batches(seed_dir, db, job):
    admitted_count = rejected = 0
    for path in sorted(seed_dir.glob('*.json')):
        if path.is_symlink() or path.stat().st_size > 128000:
            rejected += 1
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != path.stem:
            rejected += 1
            continue
        try:
            batch = json.loads(raw)
            if (set(batch) != {'schemaVersion', 'sourceUrl', 'urls'} or batch['schemaVersion'] != 1
                    or not admitted(batch['sourceUrl']) or PRIVATE_ROUTE.search(urlsplit(batch['sourceUrl']).path)):
                raise ValueError('invalid schema')
            if not isinstance(batch['urls'], list) or not 1 <= len(batch['urls']) <= 30:
                raise ValueError('invalid batch size')
            for value in batch['urls']:
                url = validate_url(value, job)
                if not url:
                    rejected += 1
                    continue
                owner = website_key(url)
                with db:
                    before = db.total_changes
                    db.execute('INSERT OR IGNORE INTO pages(url,owner,source_sha256,state,observed_at) VALUES(?,?,?,?,?)',
                               (url, owner, path.stem, 'pending', now_iso()))
                    admitted_count += db.total_changes - before
        except (ValueError, TypeError, KeyError):
            rejected += 1
    return admitted_count, rejected


def run(runtime, max_pages=4):
    runtime = Path(runtime).resolve()
    control = runtime / 'state/tutor-studio-20261017-tutor-graph-16h'
    qual = runtime / 'state' / STAGE
    from prepare_qualification_followon import validate_tutor_graph
    auth = json.loads((control / 'authorization.json').read_text())
    validate_tutor_graph(auth)
    job = json.loads((qual / 'job.json').read_text())
    if Path(job['sourceDir']).resolve() != Path(__file__).resolve().parent:
        raise ValueError('The prepared source directory changed')
    if job['authorizedDeadline'] != '2026-10-18T09:46:36+00:00' or int(job['deadlineUnix']) != 1792316796:
        raise ValueError('The fixed deadline changed')
    if file_hash(job['crawler']) != job['crawlerSha256']:
        raise ValueError('The pinned Go crawler changed')
    stage = control / 'free-context'
    stage.mkdir(mode=0o700, exist_ok=True)
    seeds = stage / 'seed-batches'
    seeds.mkdir(mode=0o700, exist_ok=True)
    db = open_ledger(stage / 'pages.sqlite')
    try:
        added, rejected = admit_batches(seeds, db, job)
        done = 0
        pending = db.execute("SELECT url,owner FROM pages WHERE state='pending' ORDER BY observed_at,url").fetchall()
        saved = (page_index([Path(p) for p in job.get('frontiers', [])],
                            [Path(p) for p in job.get('crawlRoots', [])],
                            [qual, *[Path(p) for p in job.get('fetchRoots', [])]]) if pending else {})
        for url, owner in pending:
            if done >= max_pages or time.time() >= job['deadlineUnix']:
                break
            if db.execute("SELECT count(*) FROM pages WHERE owner=? AND state IN ('submitting','succeeded','reused','held')", (owner,)).fetchone()[0] >= 3:
                with db:
                    db.execute("UPDATE pages SET state='held',error='Three-page owner allowance reached' WHERE url=?", (url,))
                continue
            old = qual / 'candidates.sqlite'
            if old.exists():
                with closing(sqlite3.connect(old)) as candidates:
                    if candidates.execute("SELECT 1 FROM fetch_claims WHERE url=? AND state IN ('held','submitting')", (url,)).fetchone():
                        with db:
                            db.execute("UPDATE pages SET state='held',error='Prior qualification fetch hold' WHERE url=?", (url,))
                        continue
            replay = saved.get(url)
            if replay and website_key(replay.get('url')) == owner:
                proof = replay['replaySource']
                with db:
                    db.execute("UPDATE pages SET state='reused',text_sha256=?,checkpoint_sha256=?,replay_proof=?,error=NULL WHERE url=?",
                               (hashlib.sha256(replay['text'].encode()).hexdigest(), proof.get('sha256'),
                                json.dumps(proof, sort_keys=True), url))
                continue
            with db:
                db.execute("UPDATE pages SET state='submitting',observed_at=? WHERE url=?", (now_iso(), url))
            try:
                key = hashlib.sha256(url.encode()).hexdigest()
                evidence = fetch(job, qual, {'url': url, 'key': key, 'site': owner, 'evidence': None})
                if website_key(evidence.get('url')) != owner:
                    raise ValueError('Fetched context left its original owner')
                content_hash = hashlib.sha256(evidence['text'].encode()).hexdigest()
                checkpoint_hash = evidence['fetchCheckpointSha256']
                with db:
                    db.execute("UPDATE pages SET state='succeeded',text_sha256=?,checkpoint_sha256=?,error=NULL WHERE url=?",
                               (content_hash, checkpoint_hash, url))
            except (OSError, ValueError, KeyError, TypeError) as error:
                with db:
                    db.execute("UPDATE pages SET state='held',error=? WHERE url=?", (type(error).__name__ + ': ' + str(error)[:180], url))
            done += 1
        counts = dict(db.execute('SELECT state,count(*) FROM pages GROUP BY state'))
        report = {'observedAt': now_iso(), 'deadline': DEADLINE, 'newlyQueued': added,
                  'rejectedInputs': rejected, 'attemptsThisRun': done, 'states': counts,
                  'qualification': 'pending_only', 'providerCalls': 0, 'paidCostUsd': 0}
        atomic_json(stage / 'status.json', report)
        return report
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.runtime_root)))
