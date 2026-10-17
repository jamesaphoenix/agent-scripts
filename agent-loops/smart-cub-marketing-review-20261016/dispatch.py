#!/usr/bin/env python3
"""One-shot Studio review queue. Observations only, no automatic publication passes."""
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import subprocess
import time

RUN = Path.home() / 'render-tmp/smart-cub-refresh-20261016'
APP = RUN / 'smart-cub/apps/prototypes/founder-videos'
STATUS = RUN / 'workflow/marketing-review-dispatch-status.json'
FROZEN = '03c83d8aae51a158de6ad10990902d98fb40d67a2853fe95fc8a2d103a8caf15'
PROTECTED = ['scripts/review-media.py', 'scripts/review-ledger.py', 'data/scripts.json',
             'data/ctas.json', 'data/cta-assignments.json']
ADMISSION_SHA = '541476d01d7010d7ed68cc612bd4f7a362f4c39c5b0d63dc0e2927c68fe2b2ad'


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ready_pair(app, identity, frozen=FROZEN):
    receipts = {}
    for aspect in ['tall', 'wide']:
        media = app / f'out/{identity}-{aspect}.mp4'
        receipt_path = media.with_suffix('.render-receipt.json')
        if not media.is_file() or not receipt_path.is_file():
            return None
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('frozenBatchSourceSnapshot') != frozen:
            return None
        output = receipt.get('output', {})
        if output.get('path') != f'out/{identity}-{aspect}.mp4':
            raise ValueError('Receipt output path does not match selected item')
        if media.stat().st_size != output.get('bytes') or sha(media) != output.get('sha256'):
            raise ValueError('Completed receipt bytes do not match media; do not review a moving render')
        receipts[aspect] = {'sha256': output['sha256'], 'receipt': str(receipt_path),
                            'receiptSha256': sha(receipt_path)}
    return receipts


def archive_previous_error(state):
    previous = {key: state.pop(key) for key in ('error', 'haltedAt') if key in state}
    if previous:
        state.setdefault('historicalHalts', []).append({**previous, 'archivedAt': now(),
            'reason': 'Explicitly resumed queue; previous halt is historical'})


def review_admission(intent):
    path = RUN / 'workflow/review_admission.py'
    if not path.is_file() or sha(path) != ADMISSION_SHA:
        raise ValueError('Shared review admission implementation changed or is absent')
    spec = importlib.util.spec_from_file_location('task_review_admission', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.admission(RUN, family='marketing', intent=intent,
        stop_path=RUN / 'workflow/marketing-revision-review-dispatch.STOP')


def configure_revision():
    global APP, STATUS, FROZEN
    if '--revision-config' not in sys.argv:
        return None
    from revision_config import load_revision
    config_path = Path(sys.argv[sys.argv.index('--revision-config') + 1])
    data, config_sha = load_revision(config_path, RUN)
    APP = Path(data['app'])
    STATUS = RUN / 'workflow/marketing-revision-review-dispatch-status.json'
    FROZEN = data['frozenSourceSnapshot']
    return config_path, data, config_sha


def revision_queue(revision):
    config_path, config, config_sha = revision
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    with STATUS.with_suffix('.lock').open('a+') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(STATUS.read_text()) if STATUS.exists() else {
            'version': 2, 'phase': 'running', 'createdAt': now(), 'completedOutputs': {},
            'revisionConfigSha256': config_sha, 'frozenSourceSnapshot': FROZEN,
            'maximumMarketingReviewProcesses': 1, 'maximumPaidRequestsPerInvocation': 1,
            'reviewEvidenceRoot': config['reviewEvidenceRoot'], 'publicationApproval': False}
        if state['revisionConfigSha256'] != config_sha:
            raise ValueError('Revision config differs from the durable checkpoint')
        if state['phase'] in ['halted', 'completed']:
            print(json.dumps({'phase': state['phase'], 'message': 'Explicit reconciliation required'}), flush=True)
            return
        archive_previous_error(state)
        state.update(pid=os.getpid(), startedAt=now(), machine=socket.gethostname())
        identities = [row['id'] for row in json.loads((APP / 'data/scripts.json').read_text())]
        assert len(identities) == 130 and len(set(identities)) == 130
        save(STATUS, state)
        last_progress = time.monotonic()
        try:
            while len(state['completedOutputs']) < 260:
                if (RUN / 'workflow/marketing-revision-review-dispatch.STOP').exists():
                    raise ValueError('Operator stop file present; no further provider admission')
                if sha(config_path) != config_sha:
                    raise ValueError('Revision config changed during observation')
                if any(sha(APP / name) != expected for name, expected in config['protectedSourceHashes'].items()):
                    raise ValueError('Protected revision implementation/context changed')
                producer = RUN / 'workflow/marketing-revision-render-dispatch-status.json'
                if producer.exists() and json.loads(producer.read_text()).get('phase') == 'halted':
                    raise ValueError('Revision renderer halted; stop new review admissions')
                ready = None
                for identity in identities:
                    for aspect in ['tall', 'wide']:
                        key = identity + ':' + aspect
                        if key in state['completedOutputs']:
                            continue
                        pair = ready_pair(APP, identity, FROZEN)
                        if pair:
                            ready = identity, aspect, key, pair[aspect]
                            break
                    if ready:
                        break
                if ready is None:
                    if time.monotonic() - last_progress > 7200:
                        raise ValueError('No new exact revision pair for two hours')
                    state.update(phase='waiting-for-render-receipts', checkedAt=now())
                    save(STATUS, state)
                    time.sleep(20)
                    continue
                identity, aspect, key, receipt = ready
                log = RUN / f'evidence/marketing-revision-review-{identity}-{aspect}.log'
                state.update(phase='reviewing', activeId=identity, activeAspect=aspect,
                             activeReceipt=receipt, activeLog=str(log), checkedAt=now())
                save(STATUS, state)
                from revision_config import model_command
                command = model_command(config, RUN, APP, identity, aspect)
                offset = log.stat().st_size if log.exists() else 0
                with review_admission({'id': identity, 'aspect': aspect,
                                       'outputSha256': receipt['sha256']}) as admission:
                    with log.open('a') as stream:
                        child = admission.popen(command, cwd=APP, stdout=stream, stderr=subprocess.STDOUT,
                                                 env={**os.environ, 'PYTHONUNBUFFERED': '1'})
                        state['activeReviewPid'] = child.pid
                        save(STATUS, state)
                        code = child.wait()
                if code:
                    raise ValueError(f'Revision reviewer failed for {key}; no automatic paid retry')
                with log.open() as stream:
                    stream.seek(offset)
                    observations = [json.loads(line) for line in stream if line.startswith('{')]
                matching = [row for row in observations if row.get('id') == identity
                            and row.get('aspect') == aspect and row.get('state') in
                            ['completed_advisory', 'skipped_exact_version_advisory']]
                if len(matching) != 1:
                    raise ValueError('Reviewer did not save exactly one complete aspect observation')
                pair = ready_pair(APP, identity, FROZEN)
                if not pair or pair[aspect] != receipt:
                    raise ValueError('Revision output or receipt changed during observation')
                state['completedOutputs'][key] = {'receipt': receipt, 'log': str(log),
                    'observedAt': now(), 'state': matching[0]['state'], 'publicationApproval': False}
                for name in ['activeId', 'activeAspect', 'activeReviewPid', 'activeReceipt', 'activeLog']:
                    state.pop(name, None)
                state.update(phase='running', completedOutputCount=len(state['completedOutputs']), checkedAt=now())
                save(STATUS, state)
                print(json.dumps({'state': 'observed_needs_agent_judgement', 'key': key,
                                  'completedOutputs': len(state['completedOutputs'])}), flush=True)
                last_progress = time.monotonic()
            state.update(phase='completed', completedAt=now(), publicationApproval=False)
            save(STATUS, state)
        except BaseException as error:
            state.update(phase='halted', haltedAt=now(), error=str(error)[:500],
                         publicationApproval=False, automaticRetry=False)
            save(STATUS, state)
            raise


def main():
    revision = configure_revision()
    if '--check' in sys.argv:
        if revision is None:
            raise ValueError('An explicit revision config is required for preflight')
        print(json.dumps({'state': 'revision_preflight_verified', 'app': str(APP),
                          'configSha256': revision[2], 'sourceSnapshot': FROZEN}))
        return
    if re.sub(r'[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise ValueError('This queue only runs on Mac Studio')
    if revision:
        revision_queue(revision)
        return
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    with STATUS.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(STATUS.read_text()) if STATUS.exists() else {
            'version': 1, 'phase': 'running', 'createdAt': now(), 'completedPairs': {},
            'frozenSourceSnapshot': FROZEN, 'maximumMarketingReviewProcesses': 1,
            'publicationApproval': False, 'protectedSourceHashes': {p: sha(APP / p) for p in PROTECTED}}
        if revision:
            if state.get('revisionConfigSha256', revision[2]) != revision[2]:
                raise ValueError('Revision config changed after checkpoint')
            state['revisionConfigSha256'] = revision[2]
        if state['phase'] in ['halted', 'completed']:
            print(json.dumps({'phase': state['phase'], 'message': 'No automatic restart of completed or halted queue'}), flush=True)
            return
        archive_previous_error(state)
        state.update({'pid': os.getpid(), 'startedAt': now(), 'machine': socket.gethostname()})
        identities = [row['id'] for row in json.loads((APP / 'data/scripts.json').read_text())]
        assert len(identities) == 130 and len(set(identities)) == 130
        save(STATUS, state)
        last_progress = time.monotonic()
        try:
            while len(state['completedPairs']) < len(identities):
                if (RUN / 'workflow/marketing-review-dispatch.STOP').exists() or (RUN / 'workflow/marketing-revision-review-dispatch.STOP').exists():
                    raise ValueError('Operator stop file present; no further provider work')
                if revision and sha(revision[0]) != revision[2]:
                    raise ValueError('Revision config changed during observation')
                if any(sha(APP / p) != expected for p, expected in state['protectedSourceHashes'].items()):
                    raise ValueError('Protected review implementation/context changed; reconcile before continuation')
                ready = None
                for identity in identities:
                    if identity in state['completedPairs']:
                        continue
                    receipts = ready_pair(APP, identity, FROZEN)
                    if receipts:
                        ready = identity, receipts
                        break
                if ready is None:
                    if time.monotonic() - last_progress > 7200:
                        raise ValueError('No newly reviewable pair for 2 hours; stop with checkpoint')
                    state.update({'phase': 'waiting-for-render-receipts', 'checkedAt': now()})
                    save(STATUS, state)
                    time.sleep(20)
                    continue
                identity, receipts = ready
                log = RUN / f"evidence/{'marketing-revision-review' if revision else 'marketing-review'}-{identity}.log"
                state.update({'phase': 'reviewing', 'activeId': identity, 'activeReceipts': receipts,
                              'activeLog': str(log), 'checkedAt': now()})
                save(STATUS, state)
                command = [str(RUN / '.venv/bin/python'), '-B', 'scripts/review-media.py', 'model',
                    '--ids', identity, '--aspect', 'both', '--max-requests', '2',
                    '--ledger', str(RUN / 'ledger/media-review-ledger.json')]
                if revision:
                    command += ['--evidence-root', revision[1]['reviewEvidenceRoot']]
                offset = log.stat().st_size if log.exists() else 0
                with log.open('a') as stream:
                    child = subprocess.Popen(command, cwd=APP, stdout=stream, stderr=subprocess.STDOUT,
                                             env={**os.environ, 'PYTHONUNBUFFERED': '1'})
                    state['activeReviewPid'] = child.pid
                    save(STATUS, state)
                    code = child.wait()
                if code:
                    raise ValueError(f'Reviewer failed for {identity}; inspect {log.name}. No automatic paid retry')
                with log.open() as stream:
                    stream.seek(offset)
                    observations = [json.loads(line) for line in stream if line.startswith('{')]
                successful = {row.get('aspect') for row in observations
                    if row.get('id') == identity and row.get('state') in
                    ['completed_advisory', 'skipped_exact_version_advisory']}
                if successful != {'tall', 'wide'}:
                    raise ValueError('Reviewer returned without both exact advisories; manual triage required')
                if ready_pair(APP, identity, FROZEN) != receipts:
                    raise ValueError('Rendered media/receipt changed during observation; final judgement withheld')
                state['completedPairs'][identity] = {'receipts': receipts, 'log': str(log),
                    'observedAt': now(), 'state': 'advisory-process-complete-agent-judgement-required',
                    'publicationApproval': False}
                state.pop('activeId', None)
                state.pop('activeReviewPid', None)
                state.update({'phase': 'running', 'completedOutputs': len(state['completedPairs']) * 2,
                              'checkedAt': now()})
                save(STATUS, state)
                print(json.dumps({'phase': state['phase'], 'completedPairs': len(state['completedPairs']),
                                  'latest': identity}), flush=True)
                last_progress = time.monotonic()
            state.update({'phase': 'completed', 'completedAt': now(), 'publicationApproval': False})
            save(STATUS, state)
        except BaseException as error:
            state.update({'phase': 'halted', 'haltedAt': now(), 'error': str(error)[:500],
                          'publicationApproval': False, 'automaticRetry': False})
            save(STATUS, state)
            raise


if __name__ == '__main__':
    main()
