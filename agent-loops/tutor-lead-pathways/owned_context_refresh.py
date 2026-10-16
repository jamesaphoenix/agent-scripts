"""Revisit settled candidates when observed own offer pages were omitted."""
import json
from urllib.parse import urlsplit

from candidate_review import candidate_urls
from discovery import professional_profile, source_host, website_key
from focused_frontier import admitted, site_identity
from lead_scope import matching_page_subjects
from qualification_context import CUE, offering_rank, select_urls


def refresh(db, cache, index, scope, job):
    changed = 0
    blocked = set(job.get('blockedUrls', []))
    rows = db.execute("""SELECT key,candidate,evidence,decision FROM candidates
        WHERE decision IS NOT NULL AND state IN
        ('subject_review','locality_review','uncertain','tutor_research_candidate')"""
    ).fetchall()
    with db:
        for row in rows:
            candidate = json.loads(row['candidate'])
            url = admitted(candidate.get('website'))
            if not url:
                continue
            host = urlsplit(url).hostname.removeprefix('www.')
            if (source_host(host) or professional_profile(url)
                    or host in {'google.com', 'sites.google.com', 'facebook.com', 'instagram.com'}):
                continue
            decision = json.loads(row['decision'])
            # National availability and query origin do not establish locality.
            if (decision.get('kind') != 'independent_tutor'
                    or decision.get('kindProbability', 0) < .65
                    or decision.get('local', 0) < .7 or decision.get('outside', 1) > .1):
                continue
            receipt = cache.db.execute('SELECT state FROM decision_requests WHERE key=?',
                                       (decision.get('decisionKey'),)).fetchone()
            if receipt is None or receipt['state'] != 'succeeded':
                continue
            owner = website_key(url)
            old = json.loads(row['evidence'] or '[]')
            if not old or any(website_key(p.get('url')) != owner for p in old):
                continue
            observed = [index[u] for u in candidate_urls(candidate) if u in index
                        and index[u].get('replaySource')
                        and website_key(index[u].get('url')) == owner]
            if not observed or not any(matching_page_subjects(p.get('text', ''), scope) for p in observed):
                continue
            previous_urls = {p['url'] for p in old}
            observed_links = {admitted(link.get('url')) for page in observed
                              for link in page.get('links', [])
                              if CUE.search(link.get('anchor', ''))}
            extra = [u for u in select_urls(candidate, index)
                     if u not in previous_urls and u not in blocked
                     and website_key(u) == owner and offering_rank(u) is not None
                     and (u in observed_links or index.get(u, {}).get('replaySource'))]
            for target in extra:
                page = index.get(target)
                if page and (not page.get('replaySource') or website_key(page.get('url')) != owner):
                    continue
                if page and page.get('url') in previous_urls:
                    continue
                claim = db.execute('SELECT state FROM fetch_claims WHERE url=?', (target,)).fetchone()
                if not page and claim and claim['state'] != 'succeeded':
                    continue
                if not page and sum(site_identity(r['url']) == site_identity(target)
                                    for r in db.execute('SELECT url FROM fetch_claims')) >= 8:
                    continue
                db.execute("""UPDATE candidates SET state='pending',
                    error='Observed own offer page expands settled context',updated_at=CURRENT_TIMESTAMP
                    WHERE key=?""", (row['key'],))
                changed += 1
                break
    return changed
