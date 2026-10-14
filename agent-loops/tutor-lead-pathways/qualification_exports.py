"""Build structured research profiles from settled decisions and replayed pages.

No provider calls. Missing email is an enrichment task, not a failed tutor.
"""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from audit_directory_identities import normalize_name
from decisions_classifier import decision_request, parse_decision, NANO_PER_INPUT_TOKEN
from directory_profiles import profile_locality_status
from discovery import atomic_json, clean_emails, file_hash, now_iso, phone_key, source_host, website_key
from lead_scope import matching_page_subjects, matching_subjects
from new_leads import classify_delta
from qualification_context import cache_path

EMAIL = re.compile(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'(?<!\d)(?:\+44|0)[\d ().-]{8,22}\d(?!\d)')
AMBIGUOUS_TOWNS = {'burnham', 'haddenham', 'penn', 'sands', 'bourton', 'denham'}


def settled_decision(db, stored, candidate, pages, question_set, scope):
    from qualify_inventory import input_evidence, VERSION
    row = db.execute('SELECT * FROM decision_requests WHERE key=?', (stored.get('decisionKey'),)).fetchone()
    if row is None or row['state'] != 'succeeded':
        raise ValueError('Decision is not settled')
    # Older observations predate the explicit subject-policy context. Both
    # contracts must still reconstruct their original paid receipt exactly.
    contracts = [decision_request(input_evidence(candidate, pages, policy), question_set, VERSION)
                 for policy in (scope, None)]
    if not any(key == stored['decisionKey'] and hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest() == row['payload_sha256']
            for payload, key, _ in contracts):
        raise ValueError('Decision contract no longer matches its owned context')
    response = json.loads(row['response'])
    tokens = response.get('usage', {}).get('input_tokens')
    if type(tokens) is not int or tokens < 1 or row['spent_nano'] != tokens * NANO_PER_INPUT_TOKEN:
        raise ValueError('Decision usage proof changed')
    result = parse_decision(response)
    if any(stored.get(k) != v for k, v in result.items()):
        raise ValueError('Stored decision differs from its provider receipt')
    return dict(result, decisionKey=stored['decisionKey'])


def extract_profile(key, candidate, pages, index, scope, towns):
    owner = website_key(candidate.get('website') or candidate.get('sourceUrl'))
    if not owner or not pages:
        raise ValueError('Missing owned context')
    replayed = []
    for page in pages:
        if website_key(page.get('url')) != owner:
            raise ValueError('Context left its original owner')
        saved = index.get(page['url'])
        if not saved or saved.get('text') != page.get('text') or not saved.get('replaySource'):
            raise ValueError('Owned page replay is missing or changed')
        replayed.append(saved)
    profile_pages = [p for p in replayed if p.get('extractionKind') == 'public_profile_fields_v1']
    if profile_pages:
        if len(profile_pages) != 1:
            raise ValueError('Ambiguous public profile identity')
        page = profile_pages[0]
        fields = json.loads(page['text'])['publicProfileFields']
        if profile_locality_status(fields) != 'advertised_bucks_locality':
            raise ValueError('Public profile locality needs review')
        name = fields['name']
        subjects = matching_subjects({'services': fields['services']}, scope)
        locality = fields['localityClaim']
        member = fields['platform'] + ':' + str(fields['personId'])
        canonical = 'tutor:' + hashlib.sha256(member.encode()).hexdigest()
    else:
        hostname = urlsplit(replayed[0]['url']).hostname.removeprefix('www.')
        if source_host(hostname):
            raise ValueError('Directory text cannot identify one tutor')
        text = ' '.join(p['text'] for p in replayed)
        name = candidate.get('name', '').strip()
        if len(normalize_name(name).split()) < 2 or normalize_name(name) not in normalize_name(text):
            raise ValueError('Owned identity needs extraction or review')
        places = [t for t in [*towns, 'Buckinghamshire', 'Bucks', 'Milton Keynes']
                  if re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', text, re.I)]
        qualified = [t for t in places if t.casefold() not in AMBIGUOUS_TOWNS]
        if not qualified:
            raise ValueError('Owned locality needs review')
        locality = qualified[0]
        subjects = sorted({s for p in replayed for s in matching_page_subjects(p['text'], scope)})
        member = 'website:' + owner
        canonical = 'research:' + hashlib.sha256(member.encode()).hexdigest()
    if not subjects:
        raise ValueError('Requested subjects are absent from the owned offer')
    emails = []
    phone = None
    # Metadata, directory neighbours and guessed address patterns are not
    # contacts. Only literal public own-site text supplies this extraction.
    if not profile_pages:
        for page in replayed:
            for email in clean_emails(EMAIL.findall(page['text'])):
                if email not in {e['email'] for e in emails}:
                    emails.append({'email': email, 'sourceUrl': page['url'], 'verified': False})
            if not phone:
                for number in PHONE.findall(page['text']):
                    if phone_key(number) and phone_key(number) == phone_key(candidate.get('phone')):
                        phone = {'value': number.strip(), 'sourceUrl': page['url'], 'verified': False}
                        break
    else:
        page = profile_pages[0]
        contacts = page.get('publicContacts') or {}
        emails = [{'email': email, 'sourceUrl': page['url'], 'verified': False}
                  for email in clean_emails(contacts.get('emails', []))]
        if phone_key(contacts.get('phone')):
            phone = {'value': contacts['phone'], 'sourceUrl': page['url'], 'verified': False}
    return {'schemaVersion': 1, 'canonicalId': canonical, 'memberKeys': [member],
        'candidateId': key, 'name': name, 'website': candidate.get('website'),
        'localityClaim': locality, 'matchedSubjects': subjects, 'services': subjects,
        'sourceUrls': [p['url'] for p in replayed], 'emails': emails,
        'phone': phone['value'] if phone else None, 'phoneEvidence': phone,
        'email': emails[0]['email'] if len(emails) == 1 else None,
        'emailStatus': 'observed' if emails else 'missing',
        'outreachEligible': False, 'independentVerified': False, 'localVerified': False,
        'status': 'model_qualified_research',
        'pageEvidence': [{'url': p['url'], 'textSha256': hashlib.sha256(p['text'].encode()).hexdigest(),
                          'sourceDocumentSha256': p.get('sourceDocumentSha256'),
                          'replaySource': p['replaySource']} for p in replayed],
        'qualification': 'Automated source replay and model qualification. Availability, ownership and consent unverified.'}


def partition_profiles(baseline, rows):
    accepted, known, held = [], [], []
    for row in rows:
        existing = [*baseline, *accepted]
        owner = website_key(row.get('website'))
        if owner and any(website_key(r.get('website')) == owner for r in existing):
            known.append(row)
            continue
        delta = classify_delta(existing, [row])
        accepted.extend(delta['new']); known.extend(delta['known']); held.extend(delta['needsReview'])
    return {'accepted': accepted, 'known': known, 'held': held}


def publish(root, job, candidates, scope, index, question_set):
    baseline_config = job.get('knownContacts')
    if not baseline_config:
        return None
    path = Path(baseline_config['path'])
    if file_hash(path) != baseline_config['sha256']:
        raise ValueError('Frozen tutor baseline changed')
    baseline = json.loads(path.read_text())
    audited = root / 'exports/audited-subject-contacts.json'
    if audited.exists():
        # Include already published manual qualifications without overwriting
        # that evidence-backed export or labelling model output human-reviewed.
        baseline += json.loads(audited.read_text())
    extracted, held = [], []
    with closing(sqlite3.connect(cache_path(root, job).as_uri() + '?mode=ro', uri=True)) as cache:
        cache.row_factory = sqlite3.Row
        for row in candidates.execute("SELECT * FROM candidates WHERE state='tutor_research_candidate' ORDER BY key"):
            try:
                candidate = json.loads(row['candidate']); pages = json.loads(row['evidence'])
                decision = settled_decision(cache, json.loads(row['decision']), candidate, pages, question_set, scope)
                if (decision['kind'] != 'independent_tutor' or decision['kindProbability'] < .9
                        or decision['niche'] < .9 or decision['local'] < .9 or decision['outside'] > .1):
                    raise ValueError('Qualification probabilities need review')
                profile = extract_profile(row['key'], candidate, pages, index, scope, job['towns'])
                profile['decision'] = decision
                extracted.append(profile)
            except (OSError, ValueError, KeyError, TypeError) as error:
                held.append({'candidateId': row['key'], 'reason': str(error), 'outreachEligible': False})
    result = partition_profiles(baseline, extracted)
    held.extend(result['held'])
    enrichment = [{'candidateId': r['candidateId'], 'canonicalId': r['canonicalId'],
                   'website': r['website'], 'sourceUrls': r['sourceUrls'],
                   'reason': 'missing_public_email', 'state': 'pending', 'outreachEligible': False}
                  for r in result['accepted'] if r['emailStatus'] == 'missing']
    output = root / 'exports/automated-qualification'
    for name, values in [('profiles', extracted), ('accepted', result['accepted']), ('known', result['known']),
                         ('held', held), ('email-enrichment-queue', enrichment)]:
        atomic_json(output / (name + '.json'), values)
    report = {'observedAt': now_iso(), 'sourceSha': job['loadedSourceSha'],
              'sourceReplayedProfiles': len(extracted), 'acceptedResearchProfiles': len(result['accepted']),
              'alreadyKnownProfiles': len(result['known']), 'heldProfiles': len(held),
              'missingEmailProfiles': len(enrichment), 'outreachEligible': 0, 'paidCalls': 0,
              'heldReasons': dict(Counter(r.get('reason', r.get('deltaHoldReason')) for r in held)),
              'qualification': 'Model-qualified research profiles, separate from the source-reviewed contact count.'}
    atomic_json(output / 'summary.json', report)
    return report
