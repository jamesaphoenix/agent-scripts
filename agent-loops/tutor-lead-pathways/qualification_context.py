"""Owned context selection and verified cache reuse for qualification passes."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit

from candidate_review import candidate_urls, primary_urls
from directory_profiles import admitted_url, decode_page, profile_record
from discovery import source_host, website_key
from focused_frontier import admitted, profile_key

CUE = re.compile(r'\b(?:about|contact|location|teacher|tutor|tuition|lesson|course|service|maths|english|11)\b', re.I)
PRIVATE_ROUTE = re.compile(r'/(?:login|signup|register|account|privacy|terms)(?:/|$)', re.I)


def context_urls(candidate, index):
    urls = candidate_urls(candidate)
    website = admitted(candidate.get('website'))
    if not website:
        return urls
    host = urlsplit(website).hostname.removeprefix('www.')
    if profile_key(website) or source_host(host) or host in {'google.com', 'sites.google.com', 'facebook.com', 'instagram.com'}:
        return urls
    owner = website_key(website)
    observed = list(urls)
    for url in urls:
        for link in index.get(url, {}).get('links', [])[:100]:
            target = admitted(link.get('url'))
            if (target and website_key(target) == owner and CUE.search(link.get('anchor', ''))
                    and not PRIVATE_ROUTE.search(urlsplit(target).path) and target not in observed):
                observed.append(target)
                if len(observed) >= 24:
                    return observed
    return observed


def select_urls(candidate, index):
    primary = set(primary_urls(candidate))
    def rank(url):
        path = urlsplit(url).path.lower()
        priority = 0 if url in primary else 1 if re.search(r'about|meet|teacher', path) else 2 if re.search(r'contact|location', path) else 3
        return priority, len(path), url
    return sorted(context_urls(candidate, index), key=rank)[:3]


def select_pages(candidate, index):
    pages, seen = [], set()
    for url in select_urls(candidate, index):
        page = index.get(url)
        if page and page.get('url') not in seen:
            pages.append(page);seen.add(page['url'])
        if len(pages) == 3:
            break
    return pages


def profile_context_index(roots, candidates):
    result = {}
    urls = {u for c in candidates for u in candidate_urls(c) if admitted_url(u)}
    for url in sorted(urls):
        path = urlsplit(url).path
        host = urlsplit(url).hostname.removeprefix('www.')
        expected = (path.split('/')[-2] if host == 'thetutorsassociation.wildapricot.org' and '/Sys/PublicProfile/' in path
                    else path.rstrip('/').rsplit('/', 1)[-1] if host in {'childcare.co.uk', 'tutorhunt.com'} else None)
        if expected is None:
            continue
        key = hashlib.sha256(url.encode()).hexdigest()
        for root in roots:
            receipt_path = Path(root) / 'receipts' / (key + '.json')
            body_path = Path(root) / 'pages' / (key + '.html')
            try:
                if receipt_path.is_symlink() or body_path.is_symlink() or receipt_path.stat().st_size > 64000 or body_path.stat().st_size > 8*1024*1024:
                    continue
                receipt = json.loads(receipt_path.read_text())
                body = body_path.read_bytes()
                if (receipt.get('url') != url or receipt.get('status') != 200 or receipt.get('error')
                        or hashlib.sha256(body).hexdigest() != receipt.get('sha256')):
                    continue
                profile = profile_record(decode_page(body, receipt.get('contentType', '')), url)
                if not profile or str(profile['personId']) != expected or profile.get('sourceUrls') != [url]:
                    continue
                fields = {k: profile.get(k) for k in ('name', 'personId', 'platform', 'localityClaim', 'services',
                    'profileCity', 'profileCounty', 'organization', 'tuitionLevels', 'website', 'locationConflict')}
                text = json.dumps({'publicProfileFields': fields}, ensure_ascii=False, sort_keys=True)
                result[url] = {'url': url, 'title': profile['name'], 'text': text, 'links': [],
                    'sourceDocumentSha256': receipt['sha256'], 'extractionKind': 'public_profile_fields_v1',
                    'replaySource': {'receipt': str(receipt_path), 'receiptSha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()}}
                break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return result


def cache_path(root, job):
    shared = job.get('sharedCache')
    if shared is None:
        return Path(root) / 'decisions.sqlite'
    if not isinstance(shared, dict) or set(shared) != {'path', 'configurationSha256'}:
        raise ValueError('Invalid shared classification cache configuration')
    path = Path(shared['path'])
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError('Shared classification cache must already exist')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        value = db.execute('SELECT value FROM decision_config WHERE id=1').fetchone()[0]
    if hashlib.sha256(value.encode()).hexdigest() != shared['configurationSha256']:
        raise ValueError('Shared classification cache configuration changed')
    return path
