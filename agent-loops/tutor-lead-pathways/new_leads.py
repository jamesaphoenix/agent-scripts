"""Compare an evidence-audited identity export with a frozen delivered baseline."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from audit_directory_identities import normalize_name
from discovery import atomic_json, file_hash, now_iso


def classify_delta(baseline, current):
    member_keys = {k for r in baseline for k in r.get('memberKeys', [r['canonicalId']])}
    source_urls = {url.rstrip('/') for row in baseline for url in row.get('sourceUrls', [])}
    def names(row):
        return set(row.get('aliases', [])) | {normalize_name(row['name'])}
    baseline_names = defaultdict(set)
    for old in baseline:
        for alias in names(old):
            baseline_names[alias].add(old.get('professionalNameHint'))
    def possible_match(row):
        left = row.get('professionalNameHint')
        for alias in names(row):
            matches = baseline_names.get(alias)
            if matches and (len(alias.split()) > 1 or not left or None in matches or '' in matches or left in matches):
                return True
        return False
    result = {'new': [], 'known': [], 'needsReview': []}
    for row in current:
        if row.get('outreachEligible'):
            raise ValueError('Research export cannot grant outreach eligibility')
        members = set(row.get('memberKeys', [row['canonicalId']]))
        if members & member_keys or any(url.rstrip('/') in source_urls for url in row.get('sourceUrls', [])):
            result['known'].append(row)
        elif possible_match(row):
            result['needsReview'].append(dict(row, deltaHoldReason='possible_baseline_name_match'))
        else:
            result['new'].append(row)
    return result


def run(baseline_path, current_path, output):
    result = classify_delta(json.loads(baseline_path.read_text()), json.loads(current_path.read_text()))
    for key, rows in result.items():
        atomic_json(output / (key + '.json'), rows)
        path = output / (key + '.csv')
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['canonicalId', 'name', 'localityClaim', 'services',
                                                       'sourceUrls', 'elevenPlusServiceClaim', 'outreachEligible'])
            writer.writeheader()
            for row in rows:
                writer.writerow({field: '; '.join(row.get(field, [])) if field in ('services', 'sourceUrls')
                                 else row.get(field, False if field in ('outreachEligible', 'elevenPlusServiceClaim') else '')
                                 for field in writer.fieldnames})
        path.chmod(0o600)
    summary = {'observedAt': now_iso(), 'baselineSha256': file_hash(baseline_path),
        'currentSha256': file_hash(current_path), 'newAcceptedResearchIdentities': len(result['new']),
        'knownIdentities': len(result['known']), 'needsBaselineMatchReview': len(result['needsReview']),
        'outreachEligible': 0}
    atomic_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.baseline, args.current, args.output_dir)))
