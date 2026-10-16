#!/usr/bin/env python3
"""Studio-owned serial blend and independent mechanical verification queue.

Installed as a registered one-shot launchd task. No API calls or quality approvals.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def atomic(path, value):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    os.replace(temporary, path)


def now():
    return datetime.now(timezone.utc).isoformat()


def require_studio():
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise ValueError('Teaching media work is restricted to Mac Studio')


def approved_cta(ledger, cta_id, aspect, digest):
    state = ledger['items'][cta_id]['aspects'][aspect]
    if state.get('state') != 'passed' or state.get('review', {}).get('output_sha256') != digest:
        raise ValueError(f'Current CTA has no exact-hash approval: {cta_id} {aspect}')


def snapshot(app, ledger_path):
    manifest = read(app / 'data/teaching-social-inputs.json')
    assignments = read(app / 'data/teaching-cta-assignments.json')
    rows = manifest['videos']
    if len(rows) != 144 or len({r['skillKey'] for r in rows}) != 144 or set(assignments) != {r['skillKey'] for r in rows}:
        raise ValueError('Expected exactly 144 unique teaching inputs and complete assignments')
    library = {r['id']: r for r in manifest['ctaLibrary']}
    files = {str(p.relative_to(app)): sha(p) for p in [
        app / 'data/teaching-social-inputs.json', app / 'data/teaching-cta-assignments.json',
        app / 'scripts/blend-teaching.py', app / 'scripts/verify-teaching-blend.py']}
    ledger = read(ledger_path)
    for cta_id in sorted(set(assignments.values())):
        if library[cta_id]['audience'] not in ['parents', 'both']:
            raise ValueError(f'Inappropriate teaching audience: {cta_id}')
        for aspect in ['wide', 'tall']:
            path = app / 'out' / f'{cta_id}-{aspect}.mp4'
            digest = sha(path)
            approved_cta(ledger, cta_id, aspect, digest)
            if read(path.with_suffix('.render-receipt.json'))['output']['sha256'] != digest:
                raise ValueError(f'CTA render receipt mismatch: {path}')
            files[str(path.relative_to(app))] = digest
    # Sources are only 3 GB in aggregate; validate all before admitting the queue.
    for row in rows:
        for aspect in ['wide', 'tall']:
            path = app / 'inputs/teaching' / row['skillKey'] / f'{aspect}.mp4'
            definition = row['media'][aspect]
            if path.stat().st_size != definition['bytes'] or sha(path) != definition['sha256']:
                raise ValueError(f'Published source mismatch: {path}')
    return {'schemaVersion': 1, 'createdAt': now(), 'files': files,
            'dispatcherSha256': sha(Path(__file__)), 'total': len(rows) * 2}, rows, assignments


def assert_frozen(app, frozen):
    for relative, digest in frozen['files'].items():
        if sha(app / relative) != digest:
            raise ValueError(f'Frozen blend dependency changed: {relative}')
    if sha(Path(__file__)) != frozen['dispatcherSha256']:
        raise ValueError('Dispatcher changed during the queue')


def reusable_mechanical(report, output_sha, manifest_sha, verifier_sha, source_sha, cta_sha):
    """Exact-version technical evidence can resume, never confers quality approval."""
    return (report.get('outputSha256') == output_sha
            and report.get('sourceSha256') == source_sha
            and report.get('ctaSha256') == cta_sha
            and report.get('manifestSha256') == manifest_sha
            and report.get('verifierSha256') == verifier_sha
            and report.get('status') == 'technical-pass-quality-review-pending'
            and report.get('qualityApproved') is False
            and report.get('decodedFrames') == report.get('expectedFrames')
            and isinstance(report.get('decodedFrames'), int) and report['decodedFrames'] > 0)


def mechanical_path(output, verifier_sha):
    versioned = output.with_suffix(f'.mechanical-verification.{verifier_sha[:20]}.json')
    legacy = output.with_suffix('.mechanical-verification.json')
    if versioned.exists():
        return versioned
    if legacy.exists() and read(legacy).get('verifierSha256') == verifier_sha:
        return legacy
    return versioned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    app, run_root = args.app.resolve(), args.run_root.resolve()
    if not args.execute:
        print(json.dumps({'state': 'plan', 'jobs': 288, 'encoder': 'videotoolbox',
                          'parallelEncodes': 1, 'minimumFreeGiB': 50, 'qualityApproved': False}))
        return
    require_studio()
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    workflow, evidence = run_root / 'workflow', run_root / 'evidence'
    workflow.mkdir(exist_ok=True)
    evidence.mkdir(exist_ok=True)
    lock = (workflow / 'teaching-blend-dispatch-status.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    status_path = workflow / 'teaching-blend-dispatch-status.json'
    ledger_path = run_root / 'ledger/media-review-ledger.json'
    status = {'state': 'preflight', 'pid': os.getpid(), 'host': socket.gethostname(),
              'startedAt': now(), 'completed': [], 'total': 288, 'qualityApproved': False}
    atomic(status_path, status)
    began = time.monotonic()
    try:
        frozen, rows, assignments = snapshot(app, ledger_path)
        checkpoint_path = workflow / 'teaching-blend-dispatch-inputs.json'
        if checkpoint_path.exists():
            previous = read(checkpoint_path)
            if previous['files'] != frozen['files'] or previous['dispatcherSha256'] != frozen['dispatcherSha256']:
                raise ValueError('Saved queue dependencies differ; explicit new checkpoint required')
        else:
            atomic(checkpoint_path, frozen)
        status['inputsSha256'] = sha(checkpoint_path)
        verifier_sha = frozen['files']['scripts/verify-teaching-blend.py']
        manifest_sha = frozen['files']['data/teaching-social-inputs.json']
        for row in rows:
            skill = row['skillKey']
            for aspect in ['wide', 'tall']:
                status.update(state='encoding', current={'id': skill, 'aspect': aspect}, updatedAt=now())
                atomic(status_path, status)
                assert_frozen(app, frozen)
                cta_id = assignments[skill]
                approved_cta(read(ledger_path), cta_id, aspect,
                             frozen['files'][f'out/{cta_id}-{aspect}.mp4'])
                if shutil.disk_usage(app / 'out').free < 50 * 1024**3:
                    raise ValueError('Studio has less than 50 GiB free')
                subprocess.run([sys.executable, 'scripts/blend-teaching.py', '--source-root',
                                'inputs/teaching', '--source-layout', 'normalized', '--id', skill,
                                '--aspect', aspect, '--encoder', 'videotoolbox', '--execute'], cwd=app, check=True)
                output = app / 'out/teaching' / f'{skill}-{aspect}-cta.mp4'
                output_sha = sha(output)
                receipt = read(output.with_suffix('.receipt.json'))
                if receipt['output']['sha256'] != output_sha:
                    raise ValueError('Output changed after its render receipt')
                technical_path = mechanical_path(output, verifier_sha)
                binding = (output_sha, manifest_sha, verifier_sha, row['media'][aspect]['sha256'],
                           frozen['files'][f'out/{cta_id}-{aspect}.mp4'])
                reusable = technical_path.exists() and reusable_mechanical(read(technical_path), *binding)
                if not reusable:
                    status.update(state='verifying', updatedAt=now())
                    atomic(status_path, status)
                    verified = subprocess.run([sys.executable, 'scripts/verify-teaching-blend.py',
                        '--id', skill, '--aspect', aspect, '--verify'], cwd=app, text=True,
                        stdout=subprocess.PIPE, check=True)
                    report = json.loads(verified.stdout)
                    item = {**report['outputs'][0], 'manifestSha256': report['manifestSha256'],
                            'verifierSha256': report['verifierSha256'], 'qualityApproved': False}
                    if not reusable_mechanical(item, *binding):
                        raise ValueError('Verifier report is not bound to current output')
                    # Versioned records preserve immutable evidence cited by prior decisions.
                    if technical_path.exists():
                        raise ValueError('Current mechanical evidence exists but cannot resume; reconcile it explicitly')
                    atomic(technical_path, item)
                    report_dir = app / 'out/review/teaching-bulk' / skill / aspect / output_sha[:20]
                    report_dir.mkdir(parents=True, exist_ok=True)
                    report_path = report_dir / f'mechanical-{verifier_sha[:20]}.json'
                    if report_path.exists():
                        raise ValueError('Versioned verifier report already exists; reconcile it explicitly')
                    atomic(report_path, report)
                assert_frozen(app, frozen)
                approved_cta(read(ledger_path), cta_id, aspect,
                             frozen['files'][f'out/{cta_id}-{aspect}.mp4'])
                status['completed'].append({'id': skill, 'aspect': aspect, 'output_sha256': output_sha,
                    'bytes': output.stat().st_size, 'mechanical_sha256': sha(technical_path),
                    'mechanical_path': str(technical_path.relative_to(app)),
                    'state': 'technical-pass-quality-review-pending'})
                status.update(updatedAt=now(), elapsedSeconds=round(time.monotonic() - began, 3),
                              freeBytes=shutil.disk_usage(app / 'out').free)
                atomic(status_path, status)
        status.update(state='complete-needs-quality-review', current=None, finishedAt=now())
        atomic(status_path, status)
        print(json.dumps({'state': status['state'], 'completed': len(status['completed']),
                          'elapsedSeconds': status['elapsedSeconds'], 'qualityApproved': False}), flush=True)
    except BaseException as error:
        status.update(state='failed', error=str(error), updatedAt=now())
        atomic(status_path, status)
        raise


if __name__ == '__main__':
    main()
