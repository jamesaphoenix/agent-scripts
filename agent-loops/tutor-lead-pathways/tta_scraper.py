#!/usr/bin/env python3
"""Collect observed public TTA profiles through the registered, cached crawler."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import urllib.parse

HOST = 'thetutorsassociation.wildapricot.org'
DIRECTORY_URL = 'https://' + HOST + '/Findatutor?tab=1'
PROFILE_PATH = re.compile(r'/Sys/PublicProfile/([1-9]\d{0,15})/6119301')
MEMBER_ID = re.compile(r'[1-9]\d{0,15}')
AGENCY = re.compile(r'\b(?:tutor doctor|sherpa[ -]?online)\b', re.I)


def profile_fields(document):
    containers = [node for node in document.walk() if node.attrs.get('id') == 'idMemberDirectoryDetailsContainer']
    if len(containers) != 1:
        return None
    fields = {}
    for node in containers[0].walk():
        if not node.has_class('fieldContainer'):
            continue
        labels = [n for n in node.walk() if n.has_class('fieldLabel')]
        bodies = [n for n in node.walk() if n.has_class('fieldBody')]
        if len(labels) != 1 or len(bodies) != 1 or labels[0].text() in fields:
            return None
        fields[labels[0].text()] = bodies[0].text()
    return fields


def advertised_website(value):
    if not value or len(value) > 2048 or re.search(r'\s', value):
        return None
    candidate = value if value.startswith(('https://', 'http://')) else 'https://' + value
    try:
        url = urllib.parse.urlsplit(candidate)
        if url.scheme not in ('http', 'https') or url.username or url.password or url.port not in (None, 80, 443):
            return None
        if not url.hostname or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}', url.hostname, re.I):
            return None
        return urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, url.query, ''))
    except ValueError:
        return None


def parse_profile(document, url):
    from directory_profiles import INSTITUTION, annotate_advert, public_person
    from discovery import clean_emails, phone_key
    match = PROFILE_PATH.fullmatch(urllib.parse.urlsplit(url).path)
    fields = profile_fields(document)
    if not match or not fields:
        return None
    first, last = fields.get('First Name', ''), fields.get('Last Name', '')
    if any(not value or len(value) > 160 or not any(c.isalpha() for c in value) for value in (first, last)):
        return None
    name = first + ' ' + last
    city, county = fields.get('City', ''), fields.get('County', '')
    subject = fields.get('Subject Specialisms', '')
    # Age/qualification labels and biographical mentions cannot supply subjects.
    services = [value.strip() for value in re.split(r'[,;]', subject) if value.strip()] if len(subject) <= 2000 else []
    organization = fields.get('Company/Organisation Name', '')
    row = public_person(HOST, match.group(1), name, ', '.join(v for v in (city, county) if v), services, url)
    row.update(profileCity=city, profileCounty=county, organization=organization,
               tuitionLevels=fields.get('Tuition Levels', ''), website=advertised_website(fields.get('Business Website', '')),
               institution=bool(INSTITUTION.search(organization) or AGENCY.search(organization)),
               contactUrl=url, contactRouteNote='Public member profile. Contact forms are not submitted; private fields are not accessed.')
    email_fields = [fields.get(label, '') for label in ('Business Email', 'Email', 'Email Address', 'Contact Email')]
    row['emails'] = clean_emails(email_fields)
    row['email'] = row['emails'][0] if row['emails'] else None
    row['phone'] = next((value for label in ('Telephone', 'Phone', 'Mobile', 'Business Phone')
                         if (value := fields.get(label, '')) and len(value) <= 60
                         and re.fullmatch(r'[+()\d\s.-]+', value) and phone_key(value)), None)
    return annotate_advert(row, description=fields.get('About', ''))


def validate_index(value):
    if (not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1
        or value.get('directoryUrl') != DIRECTORY_URL or value.get('collection') != 'public_rendered_directory'
        or type(value.get('foundCount')) is not int or not 1 <= value['foundCount'] <= 5000
        or type(value.get('pageSize')) is not int or value['pageSize'] != 50):
        raise ValueError('Expected a bounded observed public TTA directory index')
    ids, priority, pages = value.get('memberIds'), value.get('priorityIds'), value.get('pages')
    if (not isinstance(ids, list) or len(ids) != value['foundCount']
        or any(not isinstance(i, str) or not MEMBER_ID.fullmatch(i) for i in ids)
        or len(set(ids)) != len(ids) or not isinstance(priority, list)
        or any(not isinstance(i, str) or i not in ids for i in priority) or len(set(priority)) != len(priority)):
        raise ValueError('Public member IDs are missing, repeated or outside the observed index')
    expected = [{'offset': offset, 'rows': min(50, value['foundCount'] - offset)}
                for offset in range(0, value['foundCount'], 50)]
    if pages != expected or any(type(page['offset']) is not int or type(page['rows']) is not int for page in pages):
        raise ValueError('Observed public directory pagination is incomplete')
    try:
        times = [datetime.datetime.fromisoformat(value[key].replace('Z', '+00:00'))
                 for key in ('firstObservedAt', 'lastObservedAt')]
        if any(t.tzinfo is None for t in times) or times[1] < times[0]:
            raise ValueError('Unordered or naive observation times')
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError('Expected ordered public index observation times') from error
    return value


def publish(index_path, profile_root):
    from directory_profiles import admitted_url, now, save
    with index_path.open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Public index exceeded two MB')
    value = validate_index(json.loads(raw))
    priority = set(value['priorityIds'])
    ids = [*value['priorityIds'], *(i for i in value['memberIds'] if i not in priority)]
    urls = ['https://' + HOST + '/Sys/PublicProfile/' + i + '/6119301' for i in ids]
    if any(admitted_url(url) != url for url in urls):
        raise ValueError('Public profile seed left the admitted TTA paths')
    batch = (json.dumps({'urls': urls}, indent=2, ensure_ascii=False) + '\n').encode()
    digest = hashlib.sha256(batch).hexdigest()
    path = profile_root / 'seed-batches' / (digest + '.json')
    if not path.exists():
        save(path, {'urls': urls})
    if path.read_bytes() != batch:
        raise ValueError('Published TTA seed batch changed')
    report = {'observedAt': now(), 'directoryUrl': DIRECTORY_URL, 'publicProfileUrls': len(urls),
              'priorityProfileUrls': len(priority), 'indexSha256': hashlib.sha256(raw).hexdigest(),
              'batchSha256': digest, 'profileRunDir': str(profile_root), 'paidCalls': 0,
              'qualification': 'Directory entries are seeds, not qualified local contacts. The registered crawler owns robots rules, cache, deadlines and profile evidence.'}
    save(profile_root / 'sources/tutors-association.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--profile-run-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.index, args.profile_run_dir)))
