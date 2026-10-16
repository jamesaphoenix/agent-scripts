#!/usr/bin/env python3
"""One disjoint Studio teaching advisory shard, with shared admission leases.

Uses the frozen base dispatcher's receipt checks and unchanged review-media.py.
This dispatcher never grants publication approval or retries uncertain paid work.
"""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

SHARDS = 2
BASE_SHA = '7b419925ae0b1b2aa71fc3cfa11291220b1de2a669c745b4e016455e9c97a762'


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def configuration(rows, manifest_sha, index):
    """Both aspects of one lesson have one stable owner, independent of readiness."""
    if type(index) is not int or index not in range(SHARDS):
        raise ValueError('Exactly two shards, index 0 or 1, are authorised')
    skills = [row['skillKey'] for row in rows]
    if len(skills) != len(set(skills)) or not skills:
        raise ValueError('Teaching ownership needs unique non-empty manifest rows')
    return {'count': SHARDS, 'index': index, 'manifestSha256': manifest_sha,
            'ownership': 'frozen-manifest-ordinal-modulo-v1',
            'ownedKeys': [f'{skill}:{aspect}' for ordinal, skill in enumerate(skills)
                          if ordinal % SHARDS == index for aspect in ('wide', 'tall')]}


def partition(rows, manifest_sha, completed):
    configs = [configuration(rows, manifest_sha, index) for index in range(SHARDS)]
    ownership = [set(config['ownedKeys']) for config in configs]
    if ownership[0] & ownership[1]:
        raise ValueError('Shard ownership overlaps')
    if set(completed) - set.union(*ownership):
        raise ValueError('Checkpoint contains a target outside the frozen manifest')
    portions = [{key: copy.deepcopy(value) for key, value in completed.items() if key in keys}
                for keys in ownership]
    if set(portions[0]) & set(portions[1]) or {**portions[0], **portions[1]} != completed:
        raise ValueError('Partition did not preserve the exact completed checkpoint')
    return configs, portions


def validate_checkpoint(state, config, protected, wrapper_sha, admission_sha, base_sha=BASE_SHA):
    if state.get('contract') != 'teaching-review-shard/v1' or state.get('shardConfig') != config:
        raise ValueError('Shard identity, frozen manifest or ownership changed')
    expected = {'dispatcherSha256': wrapper_sha, 'admissionSha256': admission_sha,
                'baseDispatcherSha256': base_sha, 'protectedSourceHashes': protected}
    if any(state.get(key) != value for key, value in expected.items()):
        raise ValueError('Pinned review implementation or context changed')
    if set(state.get('completedOutputs', {})) - set(config['ownedKeys']):
        raise ValueError('Shard checkpoint contains another owner\'s completed target')
    return set(config['ownedKeys']) - set(state['completedOutputs'])


def validate_migration(state, status_path, wrapper_sha, guard_sha):
    proof_path = Path(state['migrationProofPath'])
    proof = read(proof_path)
    if proof.get('contract') != 'teaching-two-shard-migration/v1' \
            or proof.get('shardWrapperSha256') != wrapper_sha or proof.get('admissionSha256') != guard_sha \
            or proof.get('parentCheckpointSha256') != state['parentCheckpointSha256'] \
            or proof.get('partitionDisjointAndExactUnion') is not True \
            or proof.get('allCompletedRequestsSettled') is not True \
            or proof.get('noUnresolvedTeachingPaidIntent') is not True:
        raise ValueError('Explicit settled partition proof is missing or differs')
    proof_sha = sha(proof_path)
    if state.get('migrationProofSha256'):
        if state['migrationProofSha256'] != proof_sha:
            raise ValueError('Immutable migration proof changed')
    elif state['phase'] == 'ready':
        binding = [entry for entry in proof['newCheckpoints'] if entry['path'] == str(status_path)]
        if len(binding) != 1 or binding[0]['sha256'] != sha(status_path):
            raise ValueError('Initial shard checkpoint differs from the explicit migration proof')
    else:
        raise ValueError('Resumed shard has no pinned migration proof')
    return proof_sha


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--shard-index', type=int, choices=range(SHARDS), required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'state': 'plan-no-paid-calls', 'shardIndex': args.shard_index,
                          'maximumTeachingReviewProcesses': 2, 'maximumAllReviewProcesses': 4,
                          'sharedCapUSD': 100, 'publicationApproval': False}))
        return
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise ValueError('Teaching model reviews only run on Mac Studio')
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    app, run = args.app.resolve(), args.run_root.resolve()
    base_path = run / 'workflow/teaching-review-dispatch.py'
    if sha(base_path) != BASE_SHA:
        raise ValueError('Frozen base dispatcher differs')
    base = module('frozen_teaching_dispatch', base_path)
    admission_path = run / 'workflow/review_admission.py'
    guard_sha, wrapper_sha = sha(admission_path), sha(Path(__file__))
    status_path = run / f'workflow/teaching-review-shard-{args.shard_index}-status.json'
    stop = run / 'workflow/teaching-review-dispatch.STOP'
    authority = run / 'ledger/media-review-ledger.json'
    with status_path.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # No implicit first checkpoint: explicit settled partition migration is required.
        state = read(status_path)
        if state['phase'] in ['halted', 'completed']:
            print(json.dumps({'phase': state['phase'], 'message': 'No automatic restart'}), flush=True)
            return
        published = (app / '../../web/content/skill-videos/published.json').resolve()
        protected = base.protected_files(app, published)
        manifest = app / 'data/teaching-social-inputs.json'
        rows = read(manifest)['videos']
        if len(rows) != 144:
            raise ValueError('Expected all144 frozen teaching rows')
        config = configuration(rows, sha(manifest), args.shard_index)
        validate_checkpoint(state, config, protected, wrapper_sha, guard_sha)
        proof_sha = validate_migration(state, status_path, wrapper_sha, guard_sha)
        guard = module('teaching_review_admission', admission_path)
        state.update(phase='running', pid=os.getpid(), startedAt=now(), machine=socket.gethostname(),
                     migrationProofSha256=proof_sha)
        base.save(status_path, state)
        try:
            assignments = read(app / 'data/teaching-cta-assignments.json')
            transcripts = {row['skillKey']: row for row in read(published)['videos']}
            by_skill = {row['skillKey']: row for row in rows}
            for row in rows:
                if row['ctaId'] != assignments[row['skillKey']] or not transcripts[row['skillKey']].get('transcript'):
                    raise ValueError('Teaching context differs from approved mapping/transcript')
            for key, entry in state['completedOutputs'].items():
                skill, aspect = key.rsplit(':', 1)
                if base.ready(app, by_skill[skill], aspect, assignments, protected, authority) != entry['binding'] \
                        or sha(Path(entry['request'])) != entry['request_sha256']:
                    raise ValueError('Previously completed exact observation changed')
            last_progress = time.monotonic()
            while len(state['completedOutputs']) < len(config['ownedKeys']):
                if stop.exists():
                    raise ValueError('Operator stop file present')
                if base.protected_files(app, published) != protected or sha(Path(__file__)) != wrapper_sha \
                        or sha(admission_path) != guard_sha or sha(base_path) != BASE_SHA:
                    raise ValueError('Frozen review implementation/context changed')
                budget = read(authority)['budget']
                if budget['cap_cents'] != 10000 or budget['currency'] != 'USD':
                    raise ValueError('Authoritative combined USD100 cap changed')
                selected = None
                for key in config['ownedKeys']:
                    if key in state['completedOutputs']:
                        continue
                    skill, aspect = key.rsplit(':', 1)
                    binding = base.ready(app, by_skill[skill], aspect, assignments, protected, authority)
                    if binding:
                        selected = skill, aspect, key, binding
                        break
                if selected is None:
                    producer = read(run / 'workflow/teaching-blend-dispatch-status.json')
                    if producer['state'] in ['failed', 'complete-needs-quality-review']:
                        raise ValueError('Producer stopped without all owned exact derivatives')
                    if time.monotonic() - last_progress > 7200:
                        raise ValueError('No new owned verified derivative for two hours')
                    state.update(phase='waiting-for-mechanical-receipts', checkedAt=now())
                    base.save(status_path, state)
                    time.sleep(20)
                    continue
                skill, aspect, key, binding = selected
                identity = 'teaching:' + skill
                log = run / 'evidence' / f'teaching-review-{skill}-{aspect}.log'
                command = [str(run / '.venv/bin/python'), '-B', 'scripts/review-media.py', 'model',
                           '--ids', identity, '--aspect', aspect, '--max-requests', '1',
                           '--published-manifest', str(published), '--ledger', str(authority)]
                state.update(phase='waiting-for-admission', activeId=identity, activeAspect=aspect,
                             activeBinding=binding, activeLog=str(log), checkedAt=now())
                base.save(status_path, state)
                with guard.admission(run, family='teaching', intent={
                        'id': skill, 'aspect': aspect, 'outputSha256': binding['output_sha256']},
                        stop_path=stop) as lease:
                    if base.ready(app, by_skill[skill], aspect, assignments, protected, authority) != binding:
                        raise ValueError('Output changed while waiting for admission')
                    offset = log.stat().st_size if log.exists() else 0
                    with log.open('a') as stream:
                        child = lease.popen(command, cwd=app, stdout=stream, stderr=subprocess.STDOUT,
                                            env={**os.environ, 'PYTHONUNBUFFERED': '1'})
                        state.update(phase='reviewing', activeReviewPid=child.pid, checkedAt=now())
                        base.save(status_path, state)
                        code = child.wait()
                if code:
                    raise ValueError(f'Reviewer failed for {key}; inspect log; no automatic paid retry')
                with log.open() as stream:
                    stream.seek(offset)
                    results = [json.loads(line) for line in stream if line.startswith('{')]
                observations = [row for row in results if row.get('id') == identity and row.get('aspect') == aspect
                                and row.get('state') in ['completed_advisory', 'skipped_exact_version_advisory']]
                if len(observations) != 1 or base.ready(app, by_skill[skill], aspect, assignments, protected, authority) != binding:
                    raise ValueError('No exact complete advisory or output changed during observation')
                job_path = Path(observations[0]['evidence'])
                state['completedOutputs'][key] = {'binding': binding, 'log': str(log),
                    'request': str(job_path), 'request_sha256': sha(job_path), 'observedAt': now(),
                    'state': 'advisory-complete-agent-judgement-required', 'publicationApproval': False}
                state.update(phase='running', completedCount=len(state['completedOutputs']), checkedAt=now())
                for field in ['activeId', 'activeAspect', 'activeReviewPid', 'activeBinding']:
                    state.pop(field, None)
                base.save(status_path, state)
                print(json.dumps({'phase': 'running', 'shard': args.shard_index,
                                  'completed': state['completedCount'], 'latest': key}), flush=True)
                last_progress = time.monotonic()
            state.update(phase='completed', completedAt=now(), publicationApproval=False)
            base.save(status_path, state)
        except BaseException as error:
            state.update(phase='halted', error=str(error)[:500], haltedAt=now(),
                         publicationApproval=False, automaticRetry=False)
            base.save(status_path, state)
            raise


if __name__ == '__main__':
    main()
