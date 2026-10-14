"""Prepare search replenishment from saved plans. This module cannot dispatch searches."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import unquote

from discovery import atomic_json, now_iso
from keyword_expansion import keyword_plan, search_identity, validated_towns
from keyword_supplements import recursive_query_supplements
from lead_scope import matching_page_subjects

MAX_BACKLOG = 200
GENERAL_TUITION = {'tutor', 'private tutor', 'independent tutor', 'tuition', 'private tuition'}


def service_phrase(item, scope):
    keyword = unquote(item['payload']['keyword'])
    suffix = ' ' + item['town']
    phrase = keyword[:-len(suffix)] if keyword.endswith(suffix) else ''
    if (not re.fullmatch(r"[\w +&'()/-]{3,100}", phrase)
            or not re.search(r'\btutor\w*\b|\btuition\b', phrase, re.I)):
        return None
    return phrase if phrase.casefold() in GENERAL_TUITION or matching_page_subjects(phrase, scope) else None


def prepare_backlog(root, db, path, job, scope, yields):
    if time.time() >= job['deadlineUnix']:
        return {'state': 'deadline', 'pendingQueries': 0, 'paidSearchCalls': 0}
    if not path or not Path(path).is_file() or Path(path).is_symlink():
        return {'state': 'no_search_index', 'pendingQueries': 0, 'paidSearchCalls': 0}
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as index:
        index.row_factory = sqlite3.Row
        index.execute('BEGIN')
        saved = index.execute('SELECT value FROM research_config WHERE id=1').fetchone()
        if not saved:
            return {'state': 'no_search_configuration', 'pendingQueries': 0, 'paidSearchCalls': 0}
        config = json.loads(saved['value'])
        towns = validated_towns(config['towns'])
        if sorted(t['name'].casefold() for t in towns) != sorted(t.casefold() for t in job['towns']):
            raise ValueError('Search replenishment cannot change the approved geography')
        approved = {t['name']: t for t in towns}
        observed = {r[0] for r in index.execute('SELECT DISTINCT query_key FROM search_observations')}
        proposals = []
        for row in index.execute("SELECT * FROM search_queries WHERE generation=0 AND lane IN ('maps','organic') ORDER BY priority DESC,query_key LIMIT 5000"):
            if row['query_key'] in observed or row['town'] not in approved or row['root_key'] != row['query_key']:
                continue
            try:
                item = dict(row, payload=json.loads(row['payload']))
                phrase = service_phrase(item, scope)
                if not phrase:
                    continue
                canonical = next(q for q in keyword_plan([approved[row['town']]], [phrase]) if q['lane'] == row['lane'])
                if item['payload'] != canonical['payload'] or search_identity(row['lane'], item['payload']) != row['query_key']:
                    continue
                proposals.append(dict(canonical, queryKey=row['query_key'], priority=row['priority'],
                                      rootQueryKey=row['root_key'], generation=0))
            except (ValueError, TypeError, KeyError, StopIteration):
                continue
        recursive = recursive_query_supplements({'keywordIndexPath': str(path), 'towns': towns}, limit=5000)
        proposals.extend(q for q in recursive['queries'] if q['queryKey'] not in observed and service_phrase(q, scope))
        proposals = list({q['queryKey']: q for q in proposals}.values())
        proposals.sort(key=lambda q: (-(q['priority'] + 100 * yields.get('search:' + q['lane'] + ':' + q['town'], .5)), q['queryKey']))
        selected = proposals[:MAX_BACKLOG]
        # Preserve retired entries so a request becoming uncertain cannot return
        # to the backlog. Search observations block every submitted outcome.
        db.execute('''CREATE TABLE IF NOT EXISTS qualification_search_backlog (
            query_key TEXT PRIMARY KEY, state TEXT NOT NULL, proposal TEXT NOT NULL,
            observed_at TEXT NOT NULL)''')
        with db:
            db.execute("UPDATE qualification_search_backlog SET state='deferred' WHERE state='pending_approval'")
            db.executemany("UPDATE qualification_search_backlog SET state='observed' WHERE query_key=?", [(key,) for key in observed])
            db.executemany('''INSERT INTO qualification_search_backlog VALUES(?,'pending_approval',?,?)
                ON CONFLICT(query_key) DO UPDATE SET state=excluded.state,proposal=excluded.proposal,
                observed_at=excluded.observed_at''', [(q['queryKey'], json.dumps(q, sort_keys=True), now_iso()) for q in selected])
    result = {'state': 'awaiting_search_authorization' if selected else 'search_plan_exhausted',
              'pendingQueries': len(selected), 'eligibleQueries': len(proposals), 'maximumBacklog': MAX_BACKLOG,
              'paidSearchCalls': 0, 'requiresPaidWindowAuthorization': True, 'dispatchEnabled': False,
              'deadlineUnix': job['deadlineUnix'], 'queries': selected, 'observedAt': now_iso()}
    atomic_json(root / 'search-replenishment.json', result)
    return {k: v for k, v in result.items() if k != 'queries'}
