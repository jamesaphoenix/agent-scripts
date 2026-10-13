"""Replay public register cards with local teaching and contact evidence."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

from audit_directory_identities import normalize_name
from directory_profiles import Document, Node, decode_page
from discovery import atomic_json, clean_emails, now_iso, phone_key
from new_leads import classify_delta

BDA_URL = 'https://www.bdadyslexia.org.uk/dyslexia/tutor-list/search'
TEACHING = re.compile(r'\b(?:literacy tuition|maths tuition|study skills)\b', re.I)


def services(card):
    result = []
    for parent in card.walk():
        collecting = False
        for child in parent.children:
            if not isinstance(child, Node):
                continue
            if child.tag == 'h3':
                collecting = child.text().casefold() == 'services offered'
            elif collecting:
                result.extend(n.text() for n in child.walk() if n.tag == 'li')
    return sorted(set(result))


def parse_bda(text, receipt):
    if receipt.get('url') != BDA_URL or receipt.get('status') != 200:
        raise ValueError('Expected a successful public BDA register receipt')
    candidates, held = [], []
    for card in Document(text).root.walk():
        if not card.has_class('listing-row'):
            continue
        title = card.first(lambda n: n.tag == 'h2')
        location = card.first(lambda n: n.has_class('contact-info__icon--location'))
        if not title or not location or not re.search(r'\b(?:buckinghamshire|milton keynes)\b', location.text(), re.I):
            continue
        name, locality = title.text(), location.text()
        subject_claims = services(card)
        email_values = [n.attrs['href'].removeprefix('mailto:').split('?')[0] for n in card.walk()
                        if n.tag == 'a' and n.attrs.get('href', '').startswith('mailto:')]
        emails = clean_emails(email_values)
        phone = next((n.text() for n in card.walk() if n.has_class('contact-info__icon--phone')
                      and phone_key(n.text()) and re.fullmatch(r'[+()\d\s.-]+', n.text())), None)
        card_text = card.text().replace('\u2014', '-')
        reason = ('missing_professional_name' if len(normalize_name(name).split()) < 2 else
                  'assessment_only_without_advertised_teaching' if not any(TEACHING.search(s) for s in subject_claims) else
                  'no_advertised_contact_route' if not emails and not phone else None)
        identity_key = hashlib.sha256(json.dumps([normalize_name(name), locality, emails,
                                      phone], ensure_ascii=False).encode()).hexdigest()
        row = {'canonicalId': 'bda:' + identity_key, 'memberKeys': ['bda:' + identity_key],
               'name': name, 'localityClaim': locality, 'services': subject_claims,
               'sourceUrls': [BDA_URL], 'email': emails[0] if emails else None, 'emails': emails,
               'phone': phone, 'website': None, 'outreachEligible': False,
               'elevenPlusServiceClaim': False, 'independentVerified': False, 'localVerified': False,
               'pageEvidence': [{'url': BDA_URL, 'sha256': receipt['sha256'],
                                 'observedAt': receipt['observedAt'], 'cardTextSha256': hashlib.sha256(card_text.encode()).hexdigest()}],
               'qualification': 'Advertised specialist teaching and county claim; availability, ownership and consent unverified.'}
        if reason:
            held.append(dict(row, holdReason=reason))
        else:
            candidates.append(row)
    unique = list({row['canonicalId']: row for row in candidates}.values())
    names = Counter(normalize_name(row['name']) for row in unique)
    repeated = [dict(row, holdReason='possible_duplicate_requires_review') for row in unique
                if names[normalize_name(row['name'])] > 1]
    return [row for row in unique if names[normalize_name(row['name'])] == 1], held + repeated


def replay(page, receipt_path, baseline, output, candidate_artifact=None):
    body, receipt = page.read_bytes(), json.loads(receipt_path.read_text())
    if hashlib.sha256(body).hexdigest() != receipt.get('sha256'):
        raise ValueError('Public register body hash mismatch')
    rows, held = parse_bda(decode_page(body, receipt.get('contentType', 'text/html')), receipt)
    result = classify_delta(json.loads(baseline.read_text()), rows)
    atomic_json(output / 'contacts.json', rows)
    atomic_json(output / 'held.json', held)
    for key, values in result.items():
        atomic_json(output / (key + '.json'), values)
    with (output / 'contacts.csv').open('w', newline='') as stream:
        fields = ['canonicalId', 'name', 'email', 'phone', 'localityClaim', 'services', 'sourceUrls', 'outreachEligible']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: '; '.join(row[k]) if isinstance(row.get(k), list) else row.get(k) for k in fields})
    (output / 'contacts.csv').chmod(0o600)
    if candidate_artifact:
        leads = [dict(row, notes=[row['qualification'], 'Public register card replayed from a SHA256-verified body.']) for row in rows]
        atomic_json(candidate_artifact, {'leads': leads, 'receipt': str(receipt_path), 'receiptSha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()})
    report = {'observedAt': now_iso(), 'advertisedLocalTeachingContacts': len(rows), 'held': len(held),
              'newResearchContacts': len(result['new']), 'knownOrExistingSourceContacts': len(result['known']),
              'needsBaselineMatchReview': len(result['needsReview']), 'outreachEligible': 0, 'paidCalls': 0}
    atomic_json(output / 'summary.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('page', 'receipt', 'baseline', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--candidate-artifact', type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.page, args.receipt, args.baseline, args.output, args.candidate_artifact)))
