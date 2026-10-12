"""Publish crawler seeds and audit novelty every fifteen minutes, without spending."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sqlite3
import time
from audit_directory_identities import run as audit
from discovery import PaidCache, atomic_json, now_iso, file_hash
from new_leads import run as delta
from profile_seeds import write_seed_batch


def watch(paid_root, profile_root, baseline, stage_baseline=None):
    job = json.loads((paid_root / 'manifest.json').read_text())
    cache = PaidCache(paid_root, job['budgetCents'], job['priorExposureCents'])
    try:
        seeds = write_seed_batch(paid_root, profile_root, cache)
    finally:
        cache.db.close()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = profile_root / 'review/identity-audits' / stamp
    audited = audit(profile_root, output, None)
    novelty = delta(baseline, output / 'tutors.json', output / 'delta')
    stage_novelty = delta(stage_baseline, output / 'tutors.json', output / 'this-stage-delta') if stage_baseline else None
    value = {'observedAt': now_iso(), 'sourceSeedUrls': seeds['urls'], 'audit': audited,
             'delta': novelty, 'outputDirectory': str(output), 'paidCalls': 0,
             'baselineSha256': file_hash(baseline)}
    if stage_novelty:
        value['thisStageDelta'] = stage_novelty
    keyword_path = Path(job.get('keywordIndexPath', paid_root.parent / 'keyword-search.sqlite'))
    if keyword_path.exists():
        from keyword_expansion import SearchIndex
        try:
            index = SearchIndex(keyword_path)
            try:
                value['keywordSearch'] = index.sync_run(paid_root, job['towns'])
            finally:
                index.close()
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            value['keywordSearchError'] = type(error).__name__ + ': keyword observation failed'
    atomic_json(profile_root / 'review/latest-expansion.json', value)
    print(json.dumps(value))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--paid-run-dir', type=Path, required=True)
    parser.add_argument('--profile-run-dir', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--stage-baseline', type=Path)
    parser.add_argument('--monitor-label')
    args = parser.parse_args()
    watch(args.paid_run_dir, args.profile_run_dir, args.baseline, args.stage_baseline)
    if args.monitor_label:
        if not re.fullmatch(r'com\.jud\.bucks-private-tutors-[a-z0-9-]+-audit', args.monitor_label):
            raise ValueError('Expected a registered audit label')
        profile_job = json.loads((args.profile_run_dir / 'job.json').read_text())
        profile_status = json.loads((args.profile_run_dir / 'status.json').read_text())
        if profile_status.get('phase') == 'stage_complete' or time.time() >= profile_job['deadlineUnix']:
            subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{args.monitor_label}'],capture_output=True,timeout=10)
