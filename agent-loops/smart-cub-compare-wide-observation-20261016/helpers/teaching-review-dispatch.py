#!/usr/bin/env python3
"""One Studio teaching advisory process, incremental from verified derivatives.

Calls review-media.py unchanged. Shared budget and its uncertain-intent protection
remain authoritative. Never writes a final judgement or publication approval.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def check_binding(receipt, mechanical, row, aspect, cta_id, output_sha, cta_sha, manifest_sha,
                  blend_sha, verifier_sha):
    dependencies = receipt['dependencies']
    dependency_hash = hashlib.sha256(json.dumps(dependencies, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if (receipt.get('status') != 'rendered-needs-review' or receipt.get('skillKey') != row['skillKey']
            or receipt.get('dependencyHash') != dependency_hash
            or receipt['output']['sha256'] != output_sha
            or dependencies['source'] != row['media'][aspect]
            or dependencies['aspect'] != aspect or dependencies['ctaId'] != cta_id
            or dependencies['ctaSha256'] != cta_sha or dependencies['scriptSha256'] != blend_sha):
        raise ValueError('Completed derivative receipt does not bind current source/CTA/output')
    if (mechanical.get('outputSha256') != output_sha
            or mechanical.get('sourceSha256') != row['media'][aspect]['sha256']
            or mechanical.get('ctaSha256') != cta_sha
            or mechanical.get('manifestSha256') != manifest_sha
            or mechanical.get('verifierSha256') != verifier_sha
            or mechanical.get('status') != 'technical-pass-quality-review-pending'
            or mechanical.get('qualityApproved') is not False
            or mechanical.get('decodedFrames') != mechanical.get('expectedFrames')
            or not isinstance(mechanical.get('decodedFrames'), int) or mechanical['decodedFrames'] <= 0
            or not isinstance(mechanical.get('expectedAudioSamples'), int) or mechanical['expectedAudioSamples'] <= 0
            or not isinstance(mechanical.get('decodedAudioSamples'), int)
            or not -1 <= mechanical.get('decodedAudioSamples', -9999) - mechanical.get('expectedAudioSamples', 0) <= 1024):
        raise ValueError('Independent mechanical evidence is missing, stale or incomplete')


def ready(app, row, aspect, assignments, protected, ledger_path):
    skill, cta_id = row['skillKey'], assignments[row['skillKey']]
    output = app / 'out/teaching' / f'{skill}-{aspect}-cta.mp4'
    receipt_path = output.with_suffix('.receipt.json')
    verifier_sha = protected['scripts/verify-teaching-blend.py']
    mechanical_path = output.with_suffix(f'.mechanical-verification.{verifier_sha[:20]}.json')
    if not mechanical_path.exists():
        legacy = output.with_suffix('.mechanical-verification.json')
        if legacy.exists() and read(legacy).get('verifierSha256') == verifier_sha:
            mechanical_path = legacy
    if not all(p.is_file() for p in [output, receipt_path, mechanical_path]):
        return None
    if output.with_suffix('.lock').exists():
        return None
    cta = app / 'out' / f'{cta_id}-{aspect}.mp4'
    source = app / 'inputs/teaching' / skill / f'{aspect}.mp4'
    output_sha, cta_sha = sha(output), sha(cta)
    approval = read(ledger_path)['items'][cta_id]['aspects'][aspect]
    if approval.get('state') != 'passed' or approval.get('review', {}).get('output_sha256') != cta_sha:
        raise ValueError('Selected CTA is not currently approved at its exact hash')
    receipt, mechanical = read(receipt_path), read(mechanical_path)
    if (source.stat().st_size != row['media'][aspect]['bytes']
            or sha(source) != row['media'][aspect]['sha256']
            or receipt['sourcePath'] != str(source) or receipt['ctaPath'] != str(cta)
            or receipt['output']['bytes'] != output.stat().st_size
            or receipt['output']['path'] != str(output)
            or read(cta.with_suffix('.render-receipt.json'))['output']['sha256'] != cta_sha):
        raise ValueError('Source/CTA/output file binding mismatch')
    check_binding(receipt, mechanical, row, aspect, cta_id, output_sha, cta_sha,
                  protected['data/teaching-social-inputs.json'], protected['scripts/blend-teaching.py'],
                  protected['scripts/verify-teaching-blend.py'])
    return {'output_sha256': output_sha, 'receipt_sha256': sha(receipt_path),
            'mechanical_sha256': sha(mechanical_path), 'source_sha256': row['media'][aspect]['sha256'],
            'cta_sha256': cta_sha, 'mechanical_path': str(mechanical_path.relative_to(app))}


def protected_files(app, published):
    paths = ['scripts/review-media.py', 'scripts/review-ledger.py', 'scripts/blend-teaching.py',
             'scripts/verify-teaching-blend.py', 'data/teaching-social-inputs.json',
             'data/teaching-cta-assignments.json', 'data/ctas.json']
    return {p: sha(app / p) for p in paths} | {str(published): sha(published)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'state': 'plan-no-paid-calls', 'maximumTeachingReviewProcesses': 1,
                          'sharedCapUSD': 100, 'publicationApproval': False}))
        return
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise ValueError('Teaching model reviews only run on Mac Studio')
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    app, run_root = args.app.resolve(), args.run_root.resolve()
    status_path = run_root / 'workflow/teaching-review-dispatch-status.json'
    ledger_path = run_root / 'ledger/media-review-ledger.json'
    with status_path.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        published = (app / '../../web/content/skill-videos/published.json').resolve()
        protected = protected_files(app, published)
        state = read(status_path) if status_path.exists() else {
            'version': 1, 'phase': 'running', 'createdAt': now(), 'completedOutputs': {},
            'protectedSourceHashes': protected, 'dispatcherSha256': sha(Path(__file__)),
            'maximumTeachingReviewProcesses': 1, 'publicationApproval': False, 'automaticRetry': False}
        if state['phase'] in ['halted', 'completed']:
            print(json.dumps({'phase': state['phase'], 'message': 'No automatic restart'}), flush=True)
            return
        state.update(pid=os.getpid(), startedAt=now(), machine=socket.gethostname())
        save(status_path, state)
        try:
            if state['protectedSourceHashes'] != protected or state['dispatcherSha256'] != sha(Path(__file__)):
                raise ValueError('Saved review context/implementation changed; reconcile before continuing')
            manifest = read(app / 'data/teaching-social-inputs.json')
            assignments = read(app / 'data/teaching-cta-assignments.json')
            rows = manifest['videos']
            transcripts = {r['skillKey']: r for r in read(published)['videos']}
            if len(rows) != 144 or len({r['skillKey'] for r in rows}) != 144:
                raise ValueError('Expected complete 144-video teaching manifest')
            for row in rows:
                if row['ctaId'] != assignments[row['skillKey']] or not transcripts[row['skillKey']].get('transcript'):
                    raise ValueError('Teaching context differs from mapping or has no approved transcript')
            # On restart, previously observed bytes and mechanical evidence must still match.
            for row in rows:
                for aspect in ['wide', 'tall']:
                    key = f"{row['skillKey']}:{aspect}"
                    if key in state['completedOutputs'] and ready(app, row, aspect, assignments, protected, ledger_path) != state['completedOutputs'][key]['binding']:
                        raise ValueError('Previously observed output changed; agent judgement remains withheld')
            last_progress = time.monotonic()
            while len(state['completedOutputs']) < 288:
                if (run_root / 'workflow/teaching-review-dispatch.STOP').exists():
                    raise ValueError('Operator stop file present')
                if protected_files(app, published) != protected or sha(Path(__file__)) != state['dispatcherSha256']:
                    raise ValueError('Frozen review implementation/context changed')
                budget = read(ledger_path)['budget']
                if budget['cap_cents'] != 10000 or budget['currency'] != 'USD':
                    raise ValueError('Authoritative combined USD100 cap changed unexpectedly')
                selected = None
                for row in rows:
                    for aspect in ['wide', 'tall']:
                        key = f"{row['skillKey']}:{aspect}"
                        if key in state['completedOutputs']:
                            continue
                        binding = ready(app, row, aspect, assignments, protected, ledger_path)
                        if binding:
                            selected = row, aspect, key, binding
                            break
                    if selected:
                        break
                if selected is None:
                    blend_status = read(run_root / 'workflow/teaching-blend-dispatch-status.json')
                    if blend_status['state'] in ['failed', 'complete-needs-quality-review']:
                        raise ValueError('Blend queue stopped/completed without all expected verified derivatives')
                    if time.monotonic() - last_progress > 7200:
                        raise ValueError('No new verified teaching derivative for two hours')
                    state.update(phase='waiting-for-mechanical-receipts', checkedAt=now())
                    save(status_path, state)
                    time.sleep(20)
                    continue
                row, aspect, key, binding = selected
                processes = subprocess.check_output(['ps', '-axo', 'command'], text=True).splitlines()
                if sum('scripts/review-media.py model' in p for p in processes) >= 2:
                    raise ValueError('Two model-review processes already active; no third process permitted')
                identity = 'teaching:' + row['skillKey']
                log = run_root / 'evidence' / f"teaching-review-{row['skillKey']}-{aspect}.log"
                state.update(phase='reviewing', activeId=identity, activeAspect=aspect,
                             activeBinding=binding, activeLog=str(log), checkedAt=now())
                save(status_path, state)
                command = [str(run_root / '.venv/bin/python'), '-B', 'scripts/review-media.py', 'model',
                    '--ids', identity, '--aspect', aspect, '--max-requests', '1',
                    '--published-manifest', str(published), '--ledger', str(ledger_path)]
                offset = log.stat().st_size if log.exists() else 0
                with log.open('a') as stream:
                    child = subprocess.Popen(command, cwd=app, stdout=stream, stderr=subprocess.STDOUT,
                                             env={**os.environ, 'PYTHONUNBUFFERED': '1'})
                    state['activeReviewPid'] = child.pid
                    save(status_path, state)
                    code = child.wait()
                if code:
                    raise ValueError(f'Reviewer failed for {key}; inspect {log.name}; no automatic paid retry')
                with log.open() as stream:
                    stream.seek(offset)
                    results = [json.loads(line) for line in stream if line.startswith('{')]
                observations = [r for r in results if r.get('id') == identity and r.get('aspect') == aspect
                                and r.get('state') in ['completed_advisory', 'skipped_exact_version_advisory']]
                if len(observations) != 1 or ready(app, row, aspect, assignments, protected, ledger_path) != binding:
                    raise ValueError('No exact complete advisory or output changed during observation')
                job_path = Path(observations[0]['evidence'])
                state['completedOutputs'][key] = {'binding': binding, 'log': str(log),
                    'request': str(job_path), 'request_sha256': sha(job_path), 'observedAt': now(),
                    'state': 'advisory-complete-agent-judgement-required', 'publicationApproval': False}
                state.update(phase='running', completedCount=len(state['completedOutputs']), checkedAt=now())
                for field in ['activeId', 'activeAspect', 'activeReviewPid', 'activeBinding']:
                    state.pop(field, None)
                save(status_path, state)
                print(json.dumps({'phase': state['phase'], 'completed': state['completedCount'], 'latest': key}), flush=True)
                last_progress = time.monotonic()
            state.update(phase='completed', completedAt=now(), publicationApproval=False)
            save(status_path, state)
        except BaseException as error:
            state.update(phase='halted', error=str(error)[:500], haltedAt=now(), publicationApproval=False,
                         automaticRetry=False)
            save(status_path, state)
            raise


if __name__ == '__main__':
    main()
