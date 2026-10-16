"""Owned context selection and verified cache reuse for qualification passes."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit, unquote

from candidate_review import candidate_urls, primary_urls
from directory_profiles import admitted_url, decode_page, Document, profile_record
from discovery import source_host, website_key
from focused_frontier import admitted, profile_key

CUE = re.compile(r'\b(?:about|contact|locations?|teachers?|tutors?|tutoring|tuition|lessons?|courses?|services?|maths?|english|literacy|phonics|reading|spelling|numeracy|eleven|11)\b', re.I)
PRIVATE_ROUTE = re.compile(r'/(?:login|signup|register|account|privacy|terms)(?:/|$)', re.I)


def offering_rank(url):
    path = unquote(urlsplit(url).path).casefold()
    if re.search(r'/(?:blog|blogs|news|articles?|20\d{2})(?:/|$)|/(?:how-to|why-choose|what-is)-', path):
        return None
    for priority, terms in enumerate((
            r'maths?|english|11(?:-?plus)?|eleven-plus|primary|literacy|phonics|reading|spelling|numeracy',
            r'tutoring|tuition|lessons?', r'services?')):
        if re.search(r'(?:^|[/_-])(?:' + terms + r')(?:[/_-]|$)', path):
            return priority
    return None


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
    observed = sorted(context_urls(candidate, index), key=rank)
    selected = observed[:3]
    # A home/about/contact trio can omit the actual offer. Reserve one slot
    # for a directly observed service page while preserving existing ordering
    # whenever the selected context already covers an equally specific offer.
    offerings = [url for url in observed if offering_rank(url) is not None]
    if not offerings:
        return selected
    best = min(offerings, key=lambda url: (offering_rank(url), len(urlsplit(url).path), url))
    if any(offering_rank(url) is not None and offering_rank(url) <= offering_rank(best) for url in selected):
        return selected
    replaceable = [url for url in selected if url not in primary]
    protected_contact = [url for url in replaceable if not re.search(r'contact|location', urlsplit(url).path, re.I)]
    if protected_contact:
        replaceable = protected_contact
    if replaceable:
        remove = max(replaceable, key=lambda url: (offering_rank(url) is not None, rank(url)))
        selected[selected.index(remove)] = best
    return sorted(selected, key=rank)


def select_pages(candidate, index):
    pages, seen = [], set()
    for url in select_urls(candidate, index):
        page = index.get(url)
        if page and page.get('url') not in seen:
            pages.append(page);seen.add(page['url'])
        if len(pages) == 3:
            break
    return pages


def fallback_pages(candidate, prior, index):
    """Keep a replayable paid input when an additional context fetch fails."""
    owner = website_key(candidate.get('website') or candidate.get('sourceUrl'))
    if owner and prior and len(prior) <= 3 and all(
            website_key(page.get('url')) == owner
            and (saved := index.get(page.get('url')))
            and saved.get('replaySource')
            and saved.get('text') == page.get('text')
            and saved.get('title') == page.get('title') for page in prior):
        return prior
    return select_pages(candidate, index)


def profile_context_index(roots, candidates):
    result = {}
    urls = {u for c in candidates for u in candidate_urls(c) if admitted_url(u)}
    for url in sorted(urls):
        path = urlsplit(url).path
        host = urlsplit(url).hostname.removeprefix('www.')
        expected = (path.split('/')[-2] if host == 'thetutorsassociation.wildapricot.org' and '/Sys/PublicProfile/' in path
                    else path.rstrip('/').rsplit('/', 1)[-1] if host in {'childcare.co.uk', 'tutorhunt.com'}
                    else path.split('/')[2] if host.endswith('.universitytutor.com') and re.fullmatch(r'/tutors/[1-9]\d{0,11}', path)
                    else path.split('/')[2] if host in {'uktutors.com', 'tutordex.net'} and path.startswith('/tutor/')
                    else path.rsplit('-', 1)[-1] if host == 'tutorextra.co.uk' and path.startswith('/profile/')
                    else path.rstrip('/').rsplit('/', 1)[-1] if host == 'tutorperch.com' and path.startswith('/t/')
                    else path.rsplit('-', 1)[-1] if host == 'tuitionlist.co.uk' and path.startswith('/tutor/')
                    else hashlib.sha256(url.encode()).hexdigest() if host == 'teachtutti.co.uk' and path.startswith('/tutor/') else None)
        own_findtutors = host == 'findtutors.co.uk' and bool(re.fullmatch(
            r'/tutor/[a-z0-9-]+\.htm|/(?:tutors-[^/]+|online-[^/]+)/[^/]+-\d+/?', path))
        own_apprentus = host == 'apprentus.co.uk' and path.startswith('/private-lessons/')
        if expected is None and not (own_findtutors or own_apprentus):
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
                decoded = decode_page(body, receipt.get('contentType', ''))
                profile = profile_record(decoded, url)
                own_profile = (host in {'childcare.co.uk', 'tutorhunt.com'}
                               and profile and profile.get('ownedProfileFields') is True)
                if own_profile or host in {'uktutors.com', 'tutordex.net', 'tutorextra.co.uk', 'tutorperch.com',
                            'tuitionlist.co.uk', 'teachtutti.co.uk', 'apprentus.co.uk'} or host.endswith('.universitytutor.com'):
                    document = Document(decoded).root
                    canonicals = [n.attrs.get('content') if n.tag == 'meta' else n.attrs.get('href')
                        for n in document.walk() if (n.tag == 'meta' and n.attrs.get('property') == 'og:url')
                        or (n.tag == 'link' and n.attrs.get('rel') == 'canonical')]
                    if not canonicals or any(website_key(admitted(c)) != website_key(url) for c in canonicals):
                        continue
                if own_findtutors and not path.startswith('/tutor/'):
                    from findtutors_profile import advert_person_id
                    expected = advert_person_id(Document(decoded).root, url)
                if (not profile or profile.get('sourceUrls') != [url]
                        or (own_findtutors and path.startswith('/tutor/') and profile.get('profileRoute') != url)
                        or (not own_apprentus and not (own_findtutors and path.startswith('/tutor/')) and str(profile['personId']) != expected)):
                    continue
                fields = {k: profile.get(k) for k in ('name', 'personId', 'platform', 'localityClaim', 'services',
                    'profileCity', 'profileCounty', 'organization', 'tuitionLevels', 'website', 'locationConflict')}
                kind = 'public_profile_fields_v1'
                if own_profile or host not in {'thetutorsassociation.wildapricot.org', 'childcare.co.uk', 'tutorhunt.com'}:
                    kind = 'public_profile_fields_v2'
                    fields.update({k: profile.get(k) for k in ('evidenceKind', 'institution', 'advertisedUnavailable',
                        'nonTuitionAdvert', 'currentLocalityDeclarations', 'additionalLocalityClaims')})
                    # These quotes come from the replayed own-profile parser,
                    # never from a search snippet, another card or caller input.
                    for key in ('ownOfferExcerpt', 'ownRoleQuote'):
                        if profile.get(key):
                            fields[key] = profile[key]
                text = json.dumps({'publicProfileFields': fields}, ensure_ascii=False, sort_keys=True)
                result[url] = {'url': url, 'title': profile['name'], 'text': text, 'links': [],
                    'publicContacts': {'emails': profile.get('emails') or ([profile['email']] if profile.get('email') else []),
                                       'phone': profile.get('phone')},
                    'sourceDocumentSha256': receipt['sha256'], 'extractionKind': kind,
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
