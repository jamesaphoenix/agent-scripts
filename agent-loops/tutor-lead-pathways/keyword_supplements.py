"""Read verified recursive queries without changing a campaign or its paid ledger."""
import json
from pathlib import Path
import re
import sqlite3
import urllib.parse

from discovery import request_key
from keyword_expansion import keyword_plan, normal_keyword, search_identity, validated_towns


def recursive_query_supplements(job, excluded_keys=(), current_keys=(), limit=500):
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError('Recursive query limit must be between one and five thousand')
    path = Path(job['keywordIndexPath']).resolve()
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        saved = db.execute('SELECT value FROM research_config WHERE id=1').fetchone()
        if not saved:
            raise ValueError('Recursive search needs its immutable research configuration')
        config = json.loads(saved['value'])
        towns = validated_towns(job['towns'])
        if (sorted(towns, key=lambda t: t['name'].casefold()) != config.get('towns')
            or type(config.get('maxDepth')) is not int or not 1 <= config['maxDepth'] <= 5
            or type(config.get('maxChildren')) is not int or not 1 <= config['maxChildren'] <= 30
            or not isinstance(config.get('anchors'), list) or not config['anchors']
            or any(not isinstance(a, str) or not a.strip() for a in config['anchors'])):
            raise ValueError('Recursive search configuration differs from approved geography or limits')
        approved = {t['name']: t for t in towns}
        excluded, current = set(excluded_keys), set(current_keys)
        memo = {}
        parents_used = {}

        def lineage(key, visiting=()):
            if key in memo:
                return memo[key]
            if key in visiting or len(visiting) > config['maxDepth']:
                return False
            row = db.execute('SELECT * FROM search_queries WHERE query_key=?', (key,)).fetchone()
            if not row:
                return False
            try:
                payload = json.loads(row['payload'])
                valid = (row['lane'] in ('maps', 'organic', 'directories')
                    and search_identity(row['lane'], payload) == key
                    and normal_keyword(payload['keyword']) == row['keyword']
                    and row['town'] in approved
                    and 0 <= row['generation'] <= config['maxDepth'])
                if valid and row['generation'] == 0:
                    valid = row['root_key'] == key
                elif valid:
                    valid = False
                    parents = db.execute('''SELECT p.* FROM query_edges e JOIN search_queries p
                        ON p.query_key=e.parent_key WHERE e.child_key=?''', (key,)).fetchall()
                    for parent in parents:
                        count = db.execute('SELECT count(*) FROM query_edges WHERE parent_key=?',
                                           (parent['query_key'],)).fetchone()[0]
                        verified = db.execute("""SELECT 1 FROM search_observations WHERE query_key=?
                            AND status='succeeded' AND receipt_verified=1 LIMIT 1""", (parent['query_key'],)).fetchone()
                        if (parent['generation'] + 1 == row['generation'] and parent['root_key'] == row['root_key']
                            and parent['town'] == row['town'] and count <= config['maxChildren'] and verified
                            and lineage(parent['query_key'], visiting + (key,))):
                            valid = True
                            parents_used[key] = parent['query_key']
                            break
            except (ValueError, TypeError, KeyError, AttributeError):
                valid = False
            memo[key] = bool(valid)
            return memo[key]

        rows = db.execute('''SELECT * FROM search_queries WHERE generation>=1
            ORDER BY priority DESC,created_at,query_key LIMIT 5000''').fetchall()
        queries, held, observed = [], 0, 0
        for row in rows:
            try:
                payload = json.loads(row['payload'])
                if row['lane'] not in ('maps', 'organic') or not lineage(row['query_key']):
                    raise ValueError('Invalid recursive lineage')
                town = approved[row['town']]
                keyword = urllib.parse.unquote(payload['keyword'])
                suffix = ' ' + town['name']
                phrase = keyword[:-len(suffix)] if keyword.endswith(suffix) else ''
                if (not re.fullmatch(r"[\w +&'()/-]{3,100}", phrase)
                    or not re.search(r'\btutor\w*\b|\btuition\b', phrase, re.I)
                    or not any(re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', normal_keyword(phrase)) for a in config['anchors'])
                    or any(re.search(r'(?<!\w)' + re.escape(t['name']) + r'(?!\w)', phrase, re.I) for t in towns)):
                    raise ValueError('Invalid recursive service phrase')
                canonical = next(q for q in keyword_plan([town], [phrase]) if q['lane'] == row['lane'])
                if payload != canonical['payload']:
                    raise ValueError('Recursive query changed the approved provider payload')
                key = request_key(row['lane'], payload)
                if key in excluded:
                    continue
                if db.execute('SELECT 1 FROM search_observations WHERE query_key=? LIMIT 1',
                              (row['query_key'],)).fetchone() and key not in current:
                    observed += 1
                    continue
                queries.append(dict(canonical, tactic='recursive_keywords', queryKey=row['query_key'],
                    generation=row['generation'], priority=row['priority'], rootQueryKey=row['root_key'],
                    parentQueryKey=parents_used[row['query_key']]))
            except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
                held += 1
        # Keep this run's receipts attributable even after the shared index observes them.
        active = [q for q in queries if q['key'] in current]
        pending = [q for q in queries if q['key'] not in current][:limit]
        return {'queries': active + pending, 'heldQueries': held, 'observedQueriesExcluded': observed,
                'pendingQueries': len(pending), 'currentQueries': len(active)}
    finally:
        db.close()
