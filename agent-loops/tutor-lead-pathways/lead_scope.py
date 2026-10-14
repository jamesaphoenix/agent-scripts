#!/usr/bin/env python3
"""Optional, campaign-specific subjects. Preserve broad evidence outside the export."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata
import urllib.parse

SUBJECTS = {
    'english': re.compile(r'\b(?:english|literacy|phonics|reading|spelling|creative writing)\b', re.I),
    'maths': re.compile(r'\b(?:maths?|mathematics|numeracy|arithmetic|algebra|geometry|dyscalculia)\b', re.I),
    'verbal_reasoning': re.compile(r'\b(?:verbal[ -]+reasoning|VR)\b', re.I),
    'non_verbal_reasoning': re.compile(r'\b(?:non[ -]?verbal[ -]+reasoning|NVR)\b', re.I),
}
ELEVEN_PLUS = re.compile(r'(?<!\w)11\s*\+|\b(?:11|eleven)[ -]+plus\b', re.I)
AGE_BAND = re.compile(r'11\s*\+\s*\(secondary\)', re.I)
PRIMARY = re.compile(r'\b(?:primary(?: school| tuition| tutor| up to)?|key stage [12]|KS[12]|SATs)\b', re.I)
MUSIC = re.compile(r'\b(?:music|musical|piano|pianist|violin|guitar|singing|vocal|drums?|flute|cello|clarinet|saxophone|keyboard|ukulele|trombone|trumpet|ABRSM)\b', re.I)
OTHER_SUBJECT = re.compile(r'\b(?:science|biology|chemistry|physics|history|geography|french|spanish|german|italian|mandarin|coding|programming|psychology|sociology|art|economics|business|accounting)\b', re.I)


def load_scope(root):
    path = Path(root) / 'discovery-scope.json'
    if not path.exists():
        return None
    with path.open('rb') as stream:
        body = stream.read(8193)
    if len(body) > 8192:
        raise ValueError('Discovery scope exceeded eight KB')
    value = json.loads(body)
    fields = {'schemaVersion', 'subjects', 'includeElevenPlus', 'includePrimaryTutors', 'excludeMusicOnly', 'authorization'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['schemaVersion']) is not int
        or value['schemaVersion'] != 1 or not isinstance(value['subjects'], list)
        or not value['subjects'] or any(not isinstance(s, str) or s not in SUBJECTS for s in value['subjects'])
        or len(set(value['subjects'])) != len(value['subjects'])
        or any(type(value[key]) is not bool for key in ('includeElevenPlus', 'includePrimaryTutors', 'excludeMusicOnly'))
        or value['excludeMusicOnly'] is not True or not isinstance(value['authorization'], dict)
        or set(value['authorization']) != {'instruction', 'recordedAt'}
        or any(not isinstance(value['authorization'][key], str) or not value['authorization'][key].strip()
               for key in ('instruction', 'recordedAt'))):
        raise ValueError('Invalid discovery subject scope')
    return value


def normalize_subject_text(text):
    text = unicodedata.normalize('NFKC', text)
    text = text.replace('\u2010', '-').replace('\u2011', '-').replace('\u2013', '-')
    return AGE_BAND.sub('', text)


def services_text(row):
    services = row.get('services', [])
    if not isinstance(services, list) or len(services) > 100 or any(not isinstance(s, str) or len(s) > 2000 for s in services):
        return ''
    return normalize_subject_text(' '.join(services))


def matching_subjects(row, scope):
    text = services_text(row)
    # The identity audit also checks a FindTutors advert's own exam-preparation
    # headline. That verified claim can be absent from its separate subject tags.
    audited_exam_claim = (row.get('elevenPlusServiceClaim') is True
        and isinstance(row.get('canonicalId'), str) and bool(row['canonicalId'])
        and isinstance(row.get('pageEvidence'), list)
        and any(isinstance(e, dict) and e.get('url') in row.get('sourceUrls', [])
                and isinstance(e.get('sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', e['sha256'])
                for e in row['pageEvidence']))
    return subject_matches(text, scope, audited_exam_claim)


def matching_page_subjects(text, scope):
    """Check a bounded page without treating it as a short contact service field."""
    if not isinstance(text, str) or len(text) > 256000:
        return []
    return subject_matches(normalize_subject_text(text), scope)


def subject_matches(text, scope, audited_exam_claim=False):
    matches = []
    for name in SUBJECTS:
        candidate = SUBJECTS['non_verbal_reasoning'].sub(' ', text) if name == 'verbal_reasoning' else text
        if name in scope['subjects'] and SUBJECTS[name].search(candidate):
            matches.append(name)
    if scope['includeElevenPlus'] and (ELEVEN_PLUS.search(text) or audited_exam_claim):
        matches.append('eleven_plus')
    if not matches and scope['includePrimaryTutors'] and PRIMARY.search(text) and not MUSIC.search(text):
        matches.append('primary')
    return matches


def partition_contacts(rows, scope):
    accepted, held = [], []
    for row in rows:
        matches = matching_subjects(row, scope)
        if matches:
            accepted.append(dict(row, matchedSubjects=matches))
        else:
            text = services_text(row)
            reason = 'music_only' if MUSIC.search(text) and not OTHER_SUBJECT.search(text) else 'other_subject' if text else 'subject_unconfirmed'
            held.append(dict(row, scopeReason=reason))
    return accepted, held, {'auditedAllSubjects': len(rows), 'matchingContacts': len(accepted),
        'excludedContacts': len(held), 'excludedReasons': dict(Counter(row['scopeReason'] for row in held)),
        'matchedSubjects': dict(Counter(subject for row in accepted for subject in row['matchedSubjects'])),
        'qualification': 'Subject matches use own advertised services or an audited own 11 plus advert claim. Research only; no outreach permission inferred.'}


def query_priority(keyword, scope):
    """Specific requested subjects first, generic tutor discovery second, other subjects held."""
    text = urllib.parse.unquote(keyword)
    if matching_subjects({'services': [text]}, scope):
        return 0
    return 2 if MUSIC.search(text) or OTHER_SUBJECT.search(text) else 1


def scope_prompt(scope):
    labels = {'english': 'English', 'maths': 'maths', 'verbal_reasoning': 'verbal reasoning',
              'non_verbal_reasoning': 'non-verbal reasoning'}
    subjects = ', '.join(labels[s] for s in scope['subjects'])
    extras = (' Include explicit 11 plus exam preparation.' if scope['includeElevenPlus'] else '')
    extras += (' Primary tutors are useful even without an explicit 11 plus claim.' if scope['includePrimaryTutors'] else '')
    return (f'Research independent local tutors teaching {subjects} in Buckinghamshire and Milton Keynes.{extras} '
        'Exclude music-only teachers and tutors offering only other subjects. Retain a tutor who also teaches music '
        'when their own advert explicitly offers a requested subject. Do not infer subjects from a name, directory '
        'category, search query, age band or neighbouring advert. Make nextQueries specific to these subjects '
        'and approved towns. Keep subject evidence, source attribution and locality requirements unchanged.')


def inherit_scope(parent, child):
    scope = load_scope(parent)
    if scope is None:
        return None
    source, target = Path(parent) / 'discovery-scope.json', Path(child) / 'discovery-scope.json'
    body = source.read_bytes()
    if target.exists() and target.read_bytes() != body:
        raise ValueError('Child discovery scope changed')
    shutil.copy2(source, target)
    return {'source': str(source), 'sha256': hashlib.sha256(body).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--describe', action='store_true', required=True)
    args = parser.parse_args()
    policy = load_scope(args.run_dir)
    body = (args.run_dir / 'discovery-scope.json').read_bytes() if policy is not None else None
    print(json.dumps({'prompt': scope_prompt(policy) if policy else None,
        'sha256': hashlib.sha256(body).hexdigest() if body is not None else None}))
