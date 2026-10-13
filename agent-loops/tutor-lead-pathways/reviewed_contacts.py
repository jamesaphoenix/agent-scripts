"""Replay reviewed public adverts and own-site contacts from saved Go evidence."""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from audit_directory_identities import normalize_name
from directory_profiles import ACADEMIC, BUCKS, Document, Node, decode_page
from discovery import atomic_json, clean_emails, file_hash, now_iso, phone_key
from new_leads import classify_delta
from pdf_adverts import reviewed_unit as reviewed_pdf_unit


def normal(value):
    return ' '.join(str(value).split())


def host(value):
    parsed = urlsplit(value or '')
    if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password:
        return None
    return (parsed.hostname or '').removeprefix('www.').casefold() or None


def html_advert_unit(document, item):
    """Replay a complete element or contiguous sibling range from a hashed body."""
    selectors = [key for key in ('unitId', 'unitPath') if key in item]
    if len(selectors) != 1:
        raise ValueError('Expected one unambiguous HTML advert selector')
    if selectors[0] == 'unitId':
        units = [n for n in document.root.walk() if n.attrs.get('id') == item['unitId']]
        if len(units) != 1:
            raise ValueError('Expected one complete source advert unit')
        node, proof = units[0], {'unitId': item['unitId']}
    else:
        path = item['unitPath']
        if (not isinstance(path, list) or not 1 <= len(path) <= 20
                or any(type(index) is not int or index < 0 for index in path)):
            raise ValueError('Invalid bounded HTML advert path')
        node = document.root
        for index in path:
            if not isinstance(node, Node) or index >= len(node.children):
                raise ValueError('HTML advert path is absent')
            node = node.children[index]
        if not isinstance(node, Node):
            raise ValueError('HTML advert path must identify an element')
        proof = {'unitPath': path}
    if 'childRange' not in item:
        return node.text(), proof
    span = item['childRange']
    if (not isinstance(span, list) or len(span) != 2
            or any(type(index) is not int or index < 0 for index in span)
            or not 1 <= span[1] - span[0] <= 64 or span[1] > len(node.children)):
        raise ValueError('Invalid bounded contiguous HTML advert range')
    text = normal(' '.join(child.text() if isinstance(child, Node) else child
                          for child in node.children[span[0]:span[1]]))
    return text, dict(proof, childRange=span)


def council_advert_unit(value, record_id):
    """Select one complete active service advert from the council's public API."""
    if type(record_id) is not int or not 1 <= record_id <= 1000000000 or not isinstance(value, dict):
        raise ValueError('Invalid council service identity')
    records = value.get('content') if 'content' in value else [value]
    if not isinstance(records, list) or not 1 <= len(records) <= 100:
        raise ValueError('Expected a bounded council service response')
    matches = [r for r in records if isinstance(r, dict) and type(r.get('id')) is int and r['id'] == record_id]
    if len(matches) != 1 or matches[0].get('status') != 'active':
        raise ValueError('Expected one complete active council service advert')
    text = json.dumps(matches[0], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return text, {'recordId': record_id, 'recordSha256': hashlib.sha256(text.encode()).hexdigest()}


def verified_contact(root, item):
    if not isinstance(item, dict):
        raise ValueError('Expected a structured contact review')
    if item.get('manualHoldReason'):
        raise ValueError(item['manualHoldReason'])
    if item.get('sourceType') == 'pdf_advert':
        evidence, proof = reviewed_pdf_unit(root, item)
    elif item.get('sourceType') == 'council_advert':
        source_root = (root / 'public-source-review').resolve(strict=True)
        page = (root / item['page']).resolve(strict=True)
        receipt_path = (root / item['receipt']).resolve(strict=True)
        if not page.is_relative_to(source_root) or not receipt_path.is_relative_to(source_root):
            raise ValueError('Council evidence must belong to the campaign public-source cache')
        if file_hash(receipt_path) != item['receiptSha256']:
            raise ValueError('Council receipt hash mismatch')
        receipt = json.loads(receipt_path.read_text())
        parsed = urlsplit(receipt.get('url', ''))
        route = re.fullmatch(r'/api/v1/services(?:/([0-9]+))?/?', parsed.path)
        if (receipt.get('status') != 200 or file_hash(page) != receipt.get('sha256')
            or receipt.get('contentType', '').split(';')[0].strip() != 'application/json'
            or parsed.scheme != 'https' or parsed.netloc != 'api.familyinfo.buckinghamshire.gov.uk'
            or not route or (route[1] and route[1] != str(item['recordId']))):
            raise ValueError('Council body, response or service endpoint mismatch')
        unit_text, selector = council_advert_unit(json.loads(page.read_bytes()), item['recordId'])
        if normal(unit_text) != normal(item['unitText']) or item['sourceKind'] != 'public_advert':
            raise ValueError('Expected a complete reviewed council advert')
        source_url = 'https://directory.familyinfo.buckinghamshire.gov.uk/service/' + str(item['recordId'])
        evidence = {'url': source_url, 'contentHash': receipt['sha256'], 'text': unit_text}
        proof = {'page': str(page), 'receipt': str(receipt_path), 'receiptUrl': receipt['url'],
                 'receiptSha256': item['receiptSha256'], **selector, 'observedAt': receipt.get('observedAt')}
    elif item.get('sourceType') == 'html_advert':
        source_root = (root / 'public-source-review').resolve(strict=True)
        page = (root / item['page']).resolve(strict=True)
        receipt_path = (root / item['receipt']).resolve(strict=True)
        if not page.is_relative_to(source_root) or not receipt_path.is_relative_to(source_root):
            raise ValueError('HTML evidence must belong to the campaign public-source cache')
        if file_hash(receipt_path) != item['receiptSha256']:
            raise ValueError('HTML receipt hash mismatch')
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('status') != 200 or file_hash(page) != receipt.get('sha256'):
            raise ValueError('HTML body hash or response status mismatch')
        unit_text, selector = html_advert_unit(Document(decode_page(page.read_bytes(), receipt.get('contentType', ''))), item)
        if normal(unit_text) != normal(item['unitText']):
            raise ValueError('Expected one complete source advert unit')
        evidence = {'url': receipt['url'], 'contentHash': receipt['sha256'], 'text': unit_text}
        proof = {'page': str(page), 'receipt': str(receipt_path), 'receiptSha256': item['receiptSha256'],
                 **selector, 'observedAt': receipt.get('observedAt')}
        if item['sourceKind'] != 'public_advert':
            raise ValueError('HTML advert review requires a public advert source')
    elif item.get('sourceType') == 'focused_page':
        manifest = json.loads((root / 'manifest.json').read_text())
        source = Path(manifest['focusedParent']).resolve() / 'frontier.sqlite'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT state,evidence FROM pages WHERE key=?', (item['pageKey'],)).fetchone()
        if not row or row[0] != 'classified' or hashlib.sha256(row[1].encode()).hexdigest() != item['evidenceSha256']:
            raise ValueError('Focused page evidence hash or state mismatch')
        evidence = json.loads(row[1])
        if not evidence.get('fetchCheckpointSha256'):
            raise ValueError('Focused page lacks its original Go checkpoint proof')
        proof = {'frontier': str(source), 'pageKey': item['pageKey'], 'evidenceSha256': item['evidenceSha256'],
                 'fetchCheckpointSha256': evidence['fetchCheckpointSha256']}
    else:
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
        if result.get('error'):
            raise ValueError('Expected successful public page evidence')
        proof = {'checkpoint': str(path), 'checkpointSha256': item['checkpointSha256'], 'resultIndex': index,
                 'checkpointSavedAt': saved.get('updatedAt')}
    if not host(evidence.get('url')) or not evidence.get('contentHash'):
        raise ValueError('Expected successful public page evidence')
    text, unit = normal(evidence['text']), normal(item['unitText'])
    if not unit or len(unit) > 12000 or unit not in text:
        raise ValueError('Reviewed advert or page unit is absent from evidence')
    name, name_quote = normal(item['name']), normal(item['nameQuote'])
    town, local_quote = normal(item['localityClaim']), normal(item['localityQuote'])
    services = [normal(s) for s in item['services']]
    if not name or name not in name_quote or name_quote not in unit:
        raise ValueError('Name is not advertised in this source unit')
    locality = town.casefold()
    if (locality not in BUCKS | {'buckinghamshire', 'bucks'}
            or not re.search(r'(?<!\w)' + re.escape(locality) + r'(?!\w)', local_quote.casefold())
            or local_quote not in unit):
        raise ValueError('Missing own local town or county claim')
    if not services or any(not s or s not in unit or not ACADEMIC.search(s) for s in services):
        raise ValueError('Missing advertised academic tuition service')
    email = item.get('email')
    phone = item.get('phone')
    if email and (clean_emails([email]) != [email.lower()] or email.casefold() not in unit.casefold()):
        raise ValueError('Email is not advertised in this source unit')
    if phone and (not phone_key(phone) or normal(phone) not in unit):
        raise ValueError('Phone is not advertised in this source unit')
    source_kind = item['sourceKind']
    if source_kind not in ('own_site', 'public_advert'):
        raise ValueError('Unsupported reviewed source kind')
    website = item.get('website')
    if website and (source_kind != 'own_site' or host(website) != host(evidence['url'])):
        raise ValueError('Own-site contact must belong to the evidence host')
    if source_kind == 'own_site' and not website:
        raise ValueError('Own-site review requires its website')
    contact_url, contact_link = item.get('contactUrl'), None
    if contact_url:
        if source_kind != 'own_site' or host(contact_url) != host(website):
            raise ValueError('Contact page must belong to the reviewed own site')
        contact_link = next((link for link in evidence.get('links', [])
            if link.get('url') == contact_url
            and re.search(r'\b(?:contact|enquire|enquiry|enquiries)\b', link.get('anchor', ''), re.I)), None)
        if not contact_link:
            raise ValueError('Contact page is not advertised as a contact or enquiry link')
    if not email and not phone and not contact_link:
        raise ValueError('No advertised contact route')
    if not item.get('reviewer') or not item.get('reviewedAt'):
        raise ValueError('Missing review provenance')
    key = hashlib.sha256(json.dumps([normalize_name(name), host(website), phone_key(phone), email],
                                   ensure_ascii=False).encode()).hexdigest()
    return {'canonicalId': 'reviewed:' + key, 'memberKeys': ['reviewed:' + key], 'name': name,
        'localityClaim': town, 'services': services, 'email': email.lower() if email else None,
        'phone': phone, 'website': website, 'contactUrl': contact_url,
        'sourceUrls': [evidence['url']], 'outreachEligible': False,
        'independentVerified': False, 'localVerified': False,
        'elevenPlusServiceClaim': any(re.search(r'11\s*\+|11[ -]plus|eleven[ -]plus', s, re.I) for s in services),
        'pageEvidence': [{'url': evidence['url'], 'contentHash': evidence['contentHash'],
            **proof, 'unitTextSha256': hashlib.sha256(unit.encode()).hexdigest(),
            **({'contactLink': contact_link} if contact_link else {})}],
        'review': {'reviewer': item['reviewer'], 'reviewedAt': item['reviewedAt'], 'sourceKind': source_kind,
                   'nameQuote': name_quote, 'localityQuote': local_quote,
                   **({'notes': item['notes']} if item.get('notes') else {})},
        'qualification': 'Reviewed public advert or own-site claims. Availability, ownership and consent unverified.'}


def replay(root, reviews, baseline, output):
    candidates, held = [], []
    for item in json.loads(reviews.read_text()):
        try:
            candidates.append(verified_contact(root, item))
        except (OSError, ValueError, KeyError, IndexError, TypeError, sqlite3.Error) as error:
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
