"""Qualify isolated entries from cached public tutor rosters using the shared bank."""
import hashlib
import json
from pathlib import Path
import re
import time

from audit_directory_identities import normalize_name
from decisions_classifier import APIUnavailable, HeldDecision
from directory_profiles import Document
from discovery import atomic_json, clean_emails, file_hash, now_iso, phone_key
from focused_frontier import admitted
from lead_scope import matching_page_subjects
from new_leads import classify_delta
from qualification_exports import EMAIL, PHONE

SOURCE_URL = 'https://www.marlowmums.com/post/marlow-tutors-and-11-tutors'
NAME = re.compile(r'^Name\s*:\s*(.*?)\s+Subjects\s*:\s*(.*?)\s+Age range\s*:', re.I)
SERVICE_LOCATION = re.compile(r'\b(?:I (?:live|am based|tutor)\b|from my home\b|(?:offering|offer|provide) tuition\b|face to face\b)', re.I)


def parse_roster(html, scope, towns):
    document = Document(html)
    canonicals = [n.attrs.get('href') for n in document.root.walk()
                  if n.tag == 'link' and n.attrs.get('rel') == 'canonical']
    sections = [n for n in document.root.walk() if n.attrs.get('data-hook') == 'post-description']
    if canonicals != [SOURCE_URL] or len(sections) != 1:
        raise ValueError('Roster canonical URL or own content section changed')
    entries, current = [], None
    for node in sections[0].walk():
        if node.tag != 'p':
            continue
        text = node.text()
        match = NAME.match(text)
        if match:
            current = {'name': match[1].strip(), 'parts': [], 'links': []}
            entries.append(current)
            if len(entries) > 100:
                raise ValueError('Roster exceeds one hundred rendered entries')
        elif re.match(r'Good luck|If you know|Whether you are looking', text, re.I):
            current = None
        if current:
            current['parts'].append(text)
            current['links'].extend(n.attrs.get('href') for n in node.walk() if n.tag == 'a')
    unique = {}
    for entry in entries:
        text = ' '.join(entry['parts'])
        if len(text) > 6000:
            raise ValueError('Roster entry exceeds its own context bound')
        name = entry['name'].split(',', 1)[0].strip()
        key = hashlib.sha256((SOURCE_URL + '\n' + normalize_name(name)).encode()).hexdigest()
        locality = None
        for sentence in re.split(r'[.!?]\s+', text):
            offer = SERVICE_LOCATION.search(sentence)
            if offer:
                tail = sentence[offer.start():offer.start() + 180]
                locality = next((t for t in sorted(towns, key=len, reverse=True)
                                 if re.search(r'\b' + re.escape(t) + r'\b', tail, re.I)), None)
            if locality:
                break
        websites = list(dict.fromkeys(u for link in entry['links'] if (u := admitted(link)) and u != SOURCE_URL))
        phone = next((p.strip() for p in PHONE.findall(text) if phone_key(p)), None)
        row = {'entryId': key, 'name': name, 'ownText': text, 'localityClaim': locality,
               'matchedSubjects': matching_page_subjects(text, scope),
               'emails': clean_emails(EMAIL.findall(text)), 'phone': phone,
               'website': websites[0] if websites else None}
        old = unique.get(key)
        if old and old != row:
            raise ValueError('Repeated roster entry has conflicting own fields')
        unique[key] = row
    return list(unique.values())


def qualify_cached_rosters(root, job, db, cache, scope, baseline, stop_requested=lambda: False):
    from qualify_inventory import input_evidence
    if time.time() >= job['deadlineUnix'] or stop_requested():
        return {'state': 'deadline', 'acceptedResearchProfiles': 0}
    key = hashlib.sha256(SOURCE_URL.encode()).hexdigest()
    folder = root / 'public-rosters'
    receipt_path, body_path = folder / (key + '.json'), folder / (key + '.html')
    if not receipt_path.exists():
        return None
    if (receipt_path.is_symlink() or body_path.is_symlink() or receipt_path.stat().st_size > 64000
            or body_path.stat().st_size > 4 * 1024 * 1024):
        raise ValueError('Invalid cached roster files')
    receipt = json.loads(receipt_path.read_text()); body = body_path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if receipt.get('url') != SOURCE_URL or receipt.get('status') != 200 or receipt.get('error') or digest != receipt.get('sha256'):
        raise ValueError('Roster source receipt or body hash changed')
    entries = parse_roster(body.decode('utf-8'), scope, job['towns'])
    db.execute('''CREATE TABLE IF NOT EXISTS public_roster_qualifications (
        entry_key TEXT PRIMARY KEY, entry TEXT NOT NULL, state TEXT NOT NULL,
        decision TEXT, source_sha256 TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    accepted, known, held = [], [], []
    settled_calls, failures = 0, 0
    for entry in entries:
        if time.time() >= job['deadlineUnix'] or stop_requested():
            break
        decision = None
        reason = 'Requested subject not advertised' if not entry['matchedSubjects'] else 'Own service locality unconfirmed' if not entry['localityClaim'] else None
        row = {'schemaVersion': 1, 'canonicalId': 'roster:' + entry['entryId'], 'memberKeys': ['public-roster:' + entry['entryId']],
               'name': entry['name'], 'professionalNameHint': entry['name'], 'website': entry['website'],
               'sourceUrls': [SOURCE_URL], 'sharedSourceUrls': [SOURCE_URL],
               'localityClaim': entry['localityClaim'], 'services': entry['matchedSubjects'],
               'email': entry['emails'][0] if len(entry['emails']) == 1 else None,
               'emails': [{'email': e, 'sourceUrl': SOURCE_URL, 'verified': False} for e in entry['emails']],
               'phone': entry['phone'], 'sourceDocumentSha256': digest,
               'entryTextSha256': hashlib.sha256(entry['ownText'].encode()).hexdigest(),
               'sourceReceiptSha256': file_hash(receipt_path), 'sourceEntryId': entry['entryId'],
               'outreachEligible': False, 'availabilityVerified': False, 'independentVerified': False,
               'localVerified': False, 'status': 'research_held',
               'qualification': 'Historical public roster offer. Current availability, ownership and consent unverified.'}
        if not reason:
            text = PHONE.sub('[public phone omitted]', EMAIL.sub('[public email omitted]', entry['ownText']))
            candidate = {'name': entry['name'], 'sourceUrl': SOURCE_URL}
            pages = [{'url': SOURCE_URL, 'title': entry['name'], 'text': text}]
            try:
                decision = cache.classify(input_evidence(candidate, pages, scope))
                settled_calls += int(not decision.get('cacheHit'))
                failures = 0
                if (decision['kind'] != 'independent_tutor' or decision['kindProbability'] < .9
                        or decision['niche'] < .9 or decision['local'] < .9
                        or decision['outside'] > .1):
                    reason = 'Qualification probabilities need review'
            except HeldDecision as error:
                if isinstance(error, APIUnavailable):
                    raise
                reason = str(error); failures += 1
                if failures >= 5:
                    raise APIUnavailable('Five consecutive held roster decisions; reconciliation required')
        if reason:
            held.append(dict(row, deltaHoldReason=reason)); state = 'held'
        else:
            row['status'] = 'model_qualified_research'
            row['decision'] = decision
            delta = classify_delta([*baseline, *accepted], [row])
            accepted.extend(delta['new']); known.extend(delta['known']); held.extend(delta['needsReview'])
            state = 'accepted' if delta['new'] else 'known' if delta['known'] else 'held'
        with db:
            db.execute('''INSERT INTO public_roster_qualifications VALUES(?,?,?,?,?,?)
                ON CONFLICT(entry_key) DO UPDATE SET entry=excluded.entry,state=excluded.state,
                decision=excluded.decision,source_sha256=excluded.source_sha256,updated_at=excluded.updated_at''',
                       (entry['entryId'], json.dumps(row), state, json.dumps(decision) if decision else None, digest, now_iso()))
    output = root / 'exports/public-roster-qualification'
    for name, rows in [('accepted', accepted), ('known', known), ('held', held)]:
        atomic_json(output / (name + '.json'), rows)
    result = {'observedAt': now_iso(), 'sourceSha': job.get('loadedSourceSha'), 'sourceUrl': SOURCE_URL,
              'entries': len(entries), 'acceptedResearchProfiles': len(accepted), 'knownProfiles': len(known),
              'heldProfiles': len(held), 'settledNewDecisionCalls': settled_calls, 'sourceDocumentSha256': digest,
              'outreachEligible': 0, 'state': 'complete' if len(accepted) + len(known) + len(held) == len(entries) else 'interrupted',
              'qualification': 'Separate model-qualified public-roster research; do not add to reviewed contacts without audit.'}
    atomic_json(output / 'summary.json', result)
    return result
