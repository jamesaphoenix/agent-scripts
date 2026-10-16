#!/usr/bin/env python3
"""Scan each literal lead query completely while bounding the returned evidence."""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import unicodedata

from discovery import phone_key

FIELDS = ['name', 'website', 'phone', 'advertisedAddress']
MAX_BYTES = 4 * 1024 * 1024


def normal(value):
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return ' '.join(re.sub(r'[^\w]+', ' ', value).split())


def lookup(text, queries):
    if (not isinstance(queries, list) or not 1 <= len(queries) <= 12
        or any(not isinstance(q, str) or not q.strip() or len(q) > 160
               or any(ord(char) < 32 for char in q) for q in queries)):
        raise ValueError('Expected 1 to 12 bounded literal lead queries')
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Known lead index exceeded four MB')
    reader = csv.DictReader(io.StringIO(text), delimiter='\t')
    if reader.fieldnames != FIELDS:
        raise ValueError('Known lead index columns changed')
    results = [{'query': q, 'complete': True, 'totalMatches': 0, 'matches': [],
                'matchesTruncated': False} for q in queries]
    needles = [normal(q) for q in queries]
    phones = [phone_key(q) if re.fullmatch(r'[+0-9 ()-]+', q) else None for q in queries]
    for row in reader:
        if None in row or any(row.get(field) is None for field in FIELDS):
            raise ValueError('Malformed known lead index row')
        fields = [' ' + normal(row[field]) + ' ' for field in FIELDS]
        advertised_phones = {phone_key(value) for value in
            re.split(r'\s*/\s*|\s*[,;]\s*|\s+or\s+', row['phone'], flags=re.I)} - {None}
        for result, needle, phone in zip(results, needles, phones):
            if ((needle and any(' ' + needle + ' ' in value for value in fields))
                or (phone and phone in advertised_phones)):
                result['totalMatches'] += 1
                if len(result['matches']) < 2:
                    result['matches'].append({field: row[field][:240] for field in FIELDS})
    for result in results:
        result['matchesTruncated'] = result['totalMatches'] > len(result['matches'])
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=Path('sdk/known-leads.tsv'))
    parser.add_argument('queries', nargs='+')
    args = parser.parse_args()
    try:
        with args.index.open('rb') as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Known lead index exceeded four MB')
        print(json.dumps({'queries': lookup(raw.decode('utf-8'), args.queries)}, ensure_ascii=False))
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, type(error).__name__ + ': known lead lookup failed; absence is unproven\n')


if __name__ == '__main__':
    main()
