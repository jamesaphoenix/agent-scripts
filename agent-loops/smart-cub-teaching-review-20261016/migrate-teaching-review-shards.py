#!/usr/bin/env python3
"""Explicit settled-checkpoint partition. No reviews, rendering or launchd actions."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))
import importlib.util
spec = importlib.util.spec_from_file_location('teaching_shard', Path(__file__).with_name('teaching-review-shard.py'))
shard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shard)


def write_new(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def settled_observation(entry, job, reservation, context, current_context, ready_binding, request_sha,
                        context_digest, review_module):
    """Require exact settled evidence; a cached or merely submitted request is not success."""
    if request_sha != entry['request_sha256'] or job.get('state') != 'completed_advisory' \
            or job.get('budget_state') != 'settled_usage_tariff_estimate_rounded_up' \
            or reservation.get('state') != 'settled' \
            or context != current_context or job['version'].get('context_sha256') != context_digest \
            or ready_binding != entry['binding'] \
            or job['version'].get('output_sha256') != entry['binding']['output_sha256'] \
            or not review_module.current_review(job, job['version_key']):
        raise ValueError('Completed observation is unsettled, stale or differently bound')


def checkpoint(state, config, portion, protected, wrapper_sha, guard_sha, parent_sha, proof_path):
    return {'contract': 'teaching-review-shard/v1', 'version': 1, 'phase': 'ready',
            'createdAt': shard.now(), 'shardConfig': config, 'completedOutputs': copy.deepcopy(portion),
            'completedCount': len(portion), 'protectedSourceHashes': protected,
            'dispatcherSha256': wrapper_sha, 'baseDispatcherSha256': shard.BASE_SHA,
            'admissionSha256': guard_sha, 'maximumTeachingReviewProcesses': 2,
            'maximumAllReviewProcesses': 4, 'publicationApproval': False, 'automaticRetry': False,
            'parentCheckpointSha256': parent_sha, 'migrationProofPath': str(proof_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--run-root', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'state': 'plan-only', 'automaticRetry': False, 'publicationApproval': False}))
        return
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise ValueError('Checkpoint migration is Studio-only')
    app, run = args.app.resolve(), args.run_root.resolve()
    status = run / 'workflow/teaching-review-dispatch-status.json'
    stop = run / 'workflow/teaching-review-dispatch.STOP'
    archive = run / 'workflow/teaching-shard-migration-20261016'
    wrapper = Path(__file__).with_name('teaching-review-shard.py')
    runtime_wrapper = run / 'workflow/teaching-review-shard.py'
    guard = run / 'workflow/review_admission.py'
    base_path = run / 'workflow/teaching-review-dispatch.py'
    if shard.sha(base_path) != shard.BASE_SHA or shard.sha(wrapper) != shard.sha(runtime_wrapper):
        raise ValueError('Runtime shard/base wrapper bytes differ from migration source')
    wrapper_sha, guard_sha = shard.sha(wrapper), shard.sha(guard)
    with status.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = shard.read(status)
        if not stop.is_file() or state['phase'] != 'halted' or state.get('error') != 'Operator stop file present' \
                or any(state.get(k) for k in ('activeReviewPid', 'activeId', 'activeBinding')):
            raise ValueError('Original queue must be durably stopped between paid requests')
        if subprocess.run(['ps', '-p', str(state['pid']), '-o', 'pid='], capture_output=True).returncode == 0:
            raise ValueError('Original dispatcher PID is still alive')
        base = shard.module('migration_base_dispatch', base_path)
        published = (app / '../../web/content/skill-videos/published.json').resolve()
        protected = base.protected_files(app, published)
        if protected != state['protectedSourceHashes'] or state['dispatcherSha256'] != shard.BASE_SHA:
            raise ValueError('Original review source or dispatcher changed')
        ledger_path = run / 'ledger/media-review-ledger.json'
        authority = shard.read(ledger_path)
        if authority.get('teaching_source_overrides'):
            raise ValueError('Register corrections after base review checkpoint migration')
        media = shard.module('migration_frozen_review_media', app / 'scripts/review-media.py')
        ledger = shard.module('migration_current_ledger', app / 'scripts/review-ledger.py')
        inventory = media.inventory(app, published)
        contexts_sha = shard.digest(inventory)
        manifest = app / 'data/teaching-social-inputs.json'
        rows = shard.read(manifest)['videos']
        if len(rows) != 144:
            raise ValueError('Expected144 complete frozen input rows')
        by_skill = {row['skillKey']: row for row in rows}
        assignments = shard.read(app / 'data/teaching-cta-assignments.json')
        configs, portions = shard.partition(rows, shard.sha(manifest), state['completedOutputs'])
        observations = []
        for key, entry in state['completedOutputs'].items():
            skill, aspect = key.rsplit(':', 1)
            request = Path(entry['request'])
            job = shard.read(request)
            reservation = authority['budget']['reservations'][job['reservation_id']]
            current_context = inventory['teaching:' + skill]['context']
            binding = base.ready(app, by_skill[skill], aspect, assignments, protected, ledger_path)
            settled_observation(entry, job, reservation, shard.read(Path(job['context_path'])),
                                current_context, binding, shard.sha(request),
                                ledger.digest_json(current_context), media)
            observations.append({'key': key, 'request': str(request), 'request_sha256': shard.sha(request),
                                 'context_sha256': job['version']['context_sha256'],
                                 'output_sha256': binding['output_sha256'],
                                 'reservation_id': job['reservation_id'], 'budget_state': job['budget_state']})
        # Every other paid teaching intent must also have a terminal saved observation.
        for request in (app / 'out/review-complete').glob('teaching_*/*/*/request-*.json'):
            job = shard.read(request)
            if job.get('state') != 'completed_advisory' and (job.get('reservation_id') or job.get('state') == 'submitted_unknown'):
                raise ValueError('Unresolved paid teaching intent; never retry: ' + str(request))
        producer = run / 'workflow/teaching-blend-dispatch-inputs.json'
        producer_wrapper = run / 'workflow/teaching-blend-dispatch.py'
        producer_hashes = {str(p): shard.sha(p) for p in (producer, producer_wrapper)}
        parent_sha = shard.sha(status)
        proof_path = archive / 'resume-proof.json'
        statuses = [run / f'workflow/teaching-review-shard-{index}-status.json' for index in range(2)]
        if archive.exists() or any(path.exists() for path in statuses):
            raise ValueError('Migration/archive/shard checkpoint already exists; reconcile explicitly')
        # All validation precedes any checkpoint mutation.
        archive.mkdir()
        (archive / status.name).write_bytes(status.read_bytes())
        (archive / stop.name).write_bytes(stop.read_bytes())
        write_new(archive / 'settled-observations.json', observations)
        write_new(archive / 'protected-source-hashes.json', protected)
        new_states = [checkpoint(state, config, portion, protected, wrapper_sha, guard_sha, parent_sha, proof_path)
                      for config, portion in zip(configs, portions)]
        for path, new in zip(statuses, new_states):
            shard.validate_checkpoint(new, new['shardConfig'], protected, wrapper_sha, guard_sha)
            write_new(path, new)
        if media.inventory(app, published) != inventory or base.protected_files(app, published) != protected \
                or any(shard.sha(Path(p)) != h for p, h in producer_hashes.items()) \
                or shard.sha(status) != parent_sha:
            raise ValueError('Protected source/context/producer checkpoint changed during migration; no resume')
        proof = {'contract': 'teaching-two-shard-migration/v1', 'createdAt': shard.now(),
                 'parentCheckpointSha256': parent_sha, 'completedCount': len(observations),
                 'completedPartitionCounts': [len(portion) for portion in portions],
                 'ownedTargetCounts': [len(config['ownedKeys']) for config in configs],
                 'partitionDisjointAndExactUnion': True, 'allCompletedRequestsSettled': True,
                 'allCompletedContextAndOutputBindingsUnchanged': True, 'noUnresolvedTeachingPaidIntent': True,
                 'allOrdinaryModelInventorySha256Unchanged': contexts_sha,
                 'protectedSourceHashesUnchanged': protected, 'producerHashesUnchanged': producer_hashes,
                 'baseDispatcherSha256Unchanged': shard.BASE_SHA,
                 'shardWrapperSha256': wrapper_sha, 'admissionSha256': guard_sha,
                 'reviewImplementationSha256Unchanged': protected['scripts/review-media.py'],
                 'newCheckpoints': [{'path': str(p), 'sha256': shard.sha(p)} for p in statuses],
                 'stopRemainsPresent': True, 'publicationApproval': False, 'automaticRetry': False}
        write_new(proof_path, proof)
        # Leave original checkpoint halted and the global STOP present until registry writer confirms readiness.
        retired = copy.deepcopy(state)
        retired.update(supersededBy={'proof': str(proof_path), 'sha256': shard.sha(proof_path),
                                    'shards': [str(path) for path in statuses]}, publicationApproval=False)
        base.save(status, retired)
        print(json.dumps({'state': 'migrated-stopped-awaiting-explicit-registry-writer-kick',
                          'proof': str(proof_path), 'proof_sha256': shard.sha(proof_path),
                          'completedPartitionCounts': proof['completedPartitionCounts'],
                          'newCheckpoints': proof['newCheckpoints']}), flush=True)


if __name__ == '__main__':
    main()
