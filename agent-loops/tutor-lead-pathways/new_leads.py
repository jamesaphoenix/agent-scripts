"""Compare an evidence-audited identity export with a frozen delivered baseline."""
import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
import urllib.parse
from audit_directory_identities import normalize_name, complete_identity_hint, complete_own_name
from discovery import atomic_json, file_hash, now_iso, phone_key, clean_emails
from baseline_names import verified_name_overlay


def classify_delta(baseline, current):
    member_keys = {k for r in baseline for k in r.get('memberKeys', [r['canonicalId']])}
    source_urls = {url.rstrip('/') for row in baseline for url in row.get('sourceUrls', [])}
    def website_key(value):
        if not isinstance(value, str):
            return None
        try:
            url = urllib.parse.urlsplit(value)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                return None
            return (url.hostname.removeprefix('www.'), url.path.rstrip('/'), url.query)
        except ValueError:
            return None
    known_websites = {key for row in baseline for url in [row.get('website'), *row.get('sourceUrls', [])]
                      if (key := website_key(url))}
    def contact_phone(row):
        value = row.get('phone')
        # Prepared campaign baselines encode the original business phone in
        # their canonical key. Keep that frozen inventory unchanged while
        # comparing newly extracted professional names with business aliases.
        if not value and row.get('canonicalId', '').startswith('business:'):
            value = row['canonicalId'].rsplit(':', 1)[-1]
        return phone_key(value)
    phones = {value for row in baseline if (value := contact_phone(row))}
    def contact_email(row):
        value = row.get('email')
        valid = clean_emails([value]) if isinstance(value, str) else []
        return valid[0] if valid else None
    emails = {value for row in baseline if (value := contact_email(row))}
    def names(row):
        aliases = set(row.get('aliases', [])) | {normalize_name(row['name'])}
        if row.get('canonicalId', '').startswith('business:'):
            parts = re.split(r',|\||\s[-\u2013\u2014]\s', row['name'], maxsplit=1)
            if len(parts) > 1 and (person := complete_own_name(parts[0])):
                # A named business can already represent this person. This is
                # a possible-match alias, not proof for merging identities.
                aliases.add(person)
        hint = row.get('professionalNameHint')
        if isinstance(hint, str) and (parts := normalize_name(hint).split()) and len(parts[0]) >= 2:
            aliases.add(parts[0])
        return aliases
    baseline_names = defaultdict(set)
    full_names = defaultdict(set)
    for old in baseline:
        hint = complete_identity_hint(old.get('professionalNameHint'))
        for alias in names(old):
            baseline_names[alias].add(hint)
        if hint:
            parts = tuple(hint.split())
            full_names[parts[0], parts[-1]].add(parts)
    def possible_match(row):
        left = complete_identity_hint(row.get('professionalNameHint'))
        if left:
            parts = tuple(left.split())
            for right in full_names.get((parts[0], parts[-1]), ()):
                short, long = sorted((parts, right), key=len)
                sequence = iter(long)
                if all(part in sequence for part in short):
                    # An omitted middle name cannot prove a new person. Keep
                    # separate fully spelled names rather than merging them.
                    return True
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
        if (members & member_keys or any(url.rstrip('/') in source_urls for url in row.get('sourceUrls', []))
                or contact_phone(row) in phones):
            result['known'].append(row)
        elif (email := contact_email(row)) and email in emails:
            result['needsReview'].append(dict(row, deltaHoldReason='possible_baseline_email_match'))
        elif possible_match(row):
            result['needsReview'].append(dict(row, deltaHoldReason='possible_baseline_name_match'))
        elif (isinstance(row.get('organization'), str)
              and normalize_name(row['organization']) in baseline_names):
            result['needsReview'].append(dict(row, deltaHoldReason='possible_baseline_organisation_match'))
        elif (key := website_key(row.get('website'))) and key in known_websites:
            result['needsReview'].append(dict(row, deltaHoldReason='possible_baseline_website_match'))
        else:
            result['new'].append(row)
    return result


def run(baseline_path, current_path, output, profile_root=None):
    baseline, name_proof = verified_name_overlay(json.loads(baseline_path.read_text()), profile_root)
    result = classify_delta(baseline, json.loads(current_path.read_text()))
    for key, rows in result.items():
        atomic_json(output / (key + '.json'), rows)
        path = output / (key + '.csv')
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['canonicalId', 'name', 'email', 'phone', 'contactUrl', 'localityClaim', 'services',
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
        'outreachEligible': 0, 'baselineNamesVerified': name_proof['verifiedNames']}
    if profile_root is not None:
        atomic_json(output / 'baseline-name-evidence.json', name_proof)
    atomic_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--profile-run-dir', type=Path,
                        help='Replay missing baseline full-name hints from its frozen cached page evidence')
    args = parser.parse_args()
    print(json.dumps(run(args.baseline, args.current, args.output_dir, profile_root=args.profile_run_dir)))
