"""Replay reviewed public adverts and own-site contacts from saved Go evidence."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from audit_directory_identities import normalize_name
from directory_profiles import ACADEMIC, BUCKS
from discovery import atomic_json, clean_emails, file_hash, now_iso, phone_key
from new_leads import classify_delta


def normal(value):
    return ' '.join(str(value).split())


def host(value):
    parsed = urlsplit(value or '')
    if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password:
        return None
    return (parsed.hostname or '').removeprefix('www.').casefold() or None


def verified_contact(root, item):
    if not isinstance(item, dict):
        raise ValueError('Expected a structured contact review')
    if item.get('manualHoldReason'):
        raise ValueError(item['manualHoldReason'])
    relative = Path(item['checkpoint'])
    path = (root / relative).resolve(strict=True)
    if relative.is_absolute() or not path.is_relative_to((root / 'crawls').resolve()) or path.name != 'crawl.json':
        raise ValueError('Evidence must be a campaign crawler checkpoint')
    if file_hash(path) != item['checkpointSha256']:
        raise ValueError('Crawler checkpoint hash mismatch')
    saved = json.loads(path.read_text())
    index = item['resultIndex']
    if type(index) is not int or index < 0:
        raise ValueError('Invalid crawler result index')
    result = saved['results'][index]
    evidence = result.get('evidence') or {}
    if result.get('error') or not host(evidence.get('url')) or not evidence.get('contentHash'):
        raise ValueError('Expected successful public page evidence')
    text, unit = normal(evidence['text']), normal(item['unitText'])
    if not unit or len(unit) > 12000 or unit not in text:
        raise ValueError('Reviewed advert or page unit is absent from evidence')
    name, name_quote = normal(item['name']), normal(item['nameQuote'])
    town, local_quote = normal(item['localityClaim']), normal(item['localityQuote'])
    services = [normal(s) for s in item['services']]
    if not name or name not in name_quote or name_quote not in unit:
        raise ValueError('Name is not advertised in this source unit')
    if town.casefold() not in BUCKS or town.casefold() not in local_quote.casefold() or local_quote not in unit:
        raise ValueError('Missing own local town claim')
    if not services or any(not s or s not in unit or not ACADEMIC.search(s) for s in services):
        raise ValueError('Missing advertised academic tuition service')
    email = item.get('email')
    phone = item.get('phone')
    if email and (clean_emails([email]) != [email.lower()] or email.casefold() not in unit.casefold()):
        raise ValueError('Email is not advertised in this source unit')
    if phone and (not phone_key(phone) or normal(phone) not in unit):
        raise ValueError('Phone is not advertised in this source unit')
    if not email and not phone:
        raise ValueError('No advertised contact route')
    source_kind = item['sourceKind']
    if source_kind not in ('own_site', 'public_advert'):
        raise ValueError('Unsupported reviewed source kind')
    website = item.get('website')
    if website and (source_kind != 'own_site' or host(website) != host(evidence['url'])):
        raise ValueError('Own-site contact must belong to the evidence host')
    if source_kind == 'own_site' and not website:
        raise ValueError('Own-site review requires its website')
    if not item.get('reviewer') or not item.get('reviewedAt'):
        raise ValueError('Missing review provenance')
    key = hashlib.sha256(json.dumps([normalize_name(name), host(website), phone_key(phone), email],
                                   ensure_ascii=False).encode()).hexdigest()
    return {'canonicalId': 'reviewed:' + key, 'memberKeys': ['reviewed:' + key], 'name': name,
        'localityClaim': town, 'services': services, 'email': email.lower() if email else None,
        'phone': phone, 'website': website, 'sourceUrls': [evidence['url']], 'outreachEligible': False,
        'independentVerified': False, 'localVerified': False,
        'elevenPlusServiceClaim': any(re.search(r'11\s*\+|11[ -]plus|eleven[ -]plus', s, re.I) for s in services),
        'pageEvidence': [{'url': evidence['url'], 'contentHash': evidence['contentHash'],
            'checkpoint': str(path), 'checkpointSha256': item['checkpointSha256'], 'resultIndex': index,
            'observedAt': saved.get('updatedAt'), 'unitTextSha256': hashlib.sha256(unit.encode()).hexdigest()}],
        'review': {'reviewer': item['reviewer'], 'reviewedAt': item['reviewedAt'], 'sourceKind': source_kind,
                   'nameQuote': name_quote, 'localityQuote': local_quote},
        'qualification': 'Reviewed public advert or own-site claims. Availability, ownership and consent unverified.'}


def replay(root, reviews, baseline, output):
    candidates, held = [], []
    for item in json.loads(reviews.read_text()):
        try:
            candidates.append(verified_contact(root, item))
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            held.append({'name': item.get('name') if isinstance(item, dict) else None,
                         'holdReason': str(error), 'review': item})
    unique = list({r['canonicalId']: r for r in candidates}.values())
    counts = Counter(normalize_name(r['name']) for r in unique)
    route_counts = Counter(route for r in unique for route in
        (('phone:' + phone_key(r['phone'])) if r.get('phone') else None,
         ('email:' + r['email']) if r.get('email') else None,
         ('website:' + host(r['website'])) if r.get('website') else None) if route)
    rows = []
    for row in unique:
        shared_route = any(route_counts[route] > 1 for route in
            (('phone:' + phone_key(row['phone'])) if row.get('phone') else None,
             ('email:' + row['email']) if row.get('email') else None,
             ('website:' + host(row['website'])) if row.get('website') else None) if route)
        if counts[normalize_name(row['name'])] > 1 or shared_route:
            held.append(dict(row, holdReason='possible_duplicate_requires_review'))
        else:
            rows.append(row)
    known = json.loads(baseline.read_text())
    # Own-site pages can change paths, protocols and www aliases. Directory
    # profiles must still be matched individually, never by their shared host.
    known_hosts = {host(u) for r in known if r.get('canonicalId', '').startswith('business:') or r.get('website')
                   for u in ([r['website']] if r.get('website') else r.get('sourceUrls', []))}
    existing = [r for r in rows if r.get('website') and host(r['website']) in known_hosts]
    delta = classify_delta(known, [r for r in rows if r not in existing])
    delta['known'].extend(existing)
    atomic_json(output / 'contacts.json', rows)
    atomic_json(output / 'held.json', held)
    for key, values in delta.items():
        atomic_json(output / (key + '.json'), values)
    report = {'observedAt': now_iso(), 'reviewedPublicContacts': len(rows), 'held': len(held),
        'newResearchContacts': len(delta['new']), 'knownContacts': len(delta['known']),
        'needsBaselineMatchReview': len(delta['needsReview']), 'outreachEligible': 0, 'paidCalls': 0}
    atomic_json(output / 'summary.json', report)
    return report
