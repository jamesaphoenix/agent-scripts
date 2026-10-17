#!/usr/bin/env python3
"""One separately released, fully verified producer-state publication, no quality writes."""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).parent
TARGET_STATE = 'recovered_complete260_rendered_agent_quality_pending'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def validate_terminal(plan, terminal, fresh_pairs, log_texts, process_rows):
    if terminal.get('state') != 'recovered_complete260_rendered_verified_needs_quality_review' \
            or terminal.get('qualityApproved') is not False or len(terminal.get('completedChunks', [])) != 26:
        raise ValueError('Exact new26-chunk mechanical terminal required')
    ids = [i for chunk in plan['chunks'] for i in chunk]
    expected = {identity + ':' + aspect: fresh_pairs[identity][aspect] for identity in ids for aspect in ['wide', 'tall']}
    if len(expected) != 260 or terminal.get('outputs') != expected:
        raise ValueError('Fresh complete260 exact current output/receipt bindings required')
    groups = {terminal['pid'], terminal['dispatcherPgid']}
    all_lines = []
    for index, chunk in enumerate(terminal['completedChunks']):
        if chunk['index'] != index or chunk['ids'] != plan['chunks'][index]:
            raise ValueError('Exact ordered26 fixed controllers required')
        groups.update([chunk['controllerPid'], chunk['controllerPgid']])
        if chunk['controllerPid'] != chunk['controllerPgid'] or chunk['controllerPid'] <= 0:
            raise ValueError('Actual separate controller group identity required')
        wanted = {identity + ':' + aspect: expected[identity + ':' + aspect] for identity in chunk['ids'] for aspect in ['wide', 'tall']}
        if chunk['outputs'] != wanted:
            raise ValueError('Chunk/current output bindings differ')
        lines = log_texts[index].splitlines(); all_lines.extend(lines)
        for prior in plan['preservedOutputs']:
            if prior['id'].rsplit('-', 1)[0] in chunk['ids'] and 'SKIP verified ' + prior['id'] not in lines:
                raise ValueError('All91 preserved outputs need actual native verified skips')
    for identity in ['p9-after-a-bad-mock', 't29-ratio-as-shares']:
        for aspect in ['wide', 'tall']:
            if 'SKIP verified ' + identity + '-' + aspect not in all_lines:
                raise ValueError('All4 actual pilot native skips required')
    if min(groups) <= 0 or any(any(v in groups for v in row) for row in process_rows):
        raise ValueError('New rendering worker/controller/descendant/group still active or reused')
    return expected


def cas_publish(target, expected_sha, value, atomic_save):
    if sha(target) != expected_sha:
        raise ValueError('Original producer status changed; refuse CAS publication')
    atomic_save(target, value)


def execute(plan_path, release_path, release_sha):
    spec = importlib.util.spec_from_file_location('pinned_render_recovery', HERE / 'render.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    release = read(release_path)
    if sha(release_path) != release_sha or release.get('contract') != 'root-conditional-marketing-producer-reconciliation/v1' \
            or release.get('planSha256') != sha(plan_path) or release.get('reconcilerSha256') != sha(__file__) \
            or release.get('targetState') != TARGET_STATE or release.get('allowExactlyOneConditionalPublication') is not True:
        raise ValueError('Separate exact Root conditional reconciliation release required')
    plan, original, config = module.check(plan_path)
    terminal_path = Path(plan['status']); terminal_ref = module.ref(terminal_path); terminal = read(terminal_path)
    if terminal.get('plan') != module.ref(plan_path):
        raise ValueError('New terminal must bind exact released render plan')
    target = Path(plan['originalFailedStatus']['path'])
    if target != module.RUN / 'workflow/marketing-successor-20261017/render-status.json' \
            or release.get('originalFailedStatusSha256') != plan['originalFailedStatus']['sha256']:
        raise ValueError('Only exact original dynamic marketing producer path may be reconciled')
    evidence_dir = terminal_path.parent
    with target.with_suffix('.lock').open('a+') as target_lock:
        fcntl.flock(target_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (evidence_dir / 'conditional-reconciliation.lock').open('a+') as one_lock:
            fcntl.flock(one_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            intent = evidence_dir / 'conditional-reconciliation-intent.json'
            if intent.exists():
                raise ValueError('Existing reconciliation intent; never automatically retry')
            module.current(plan['originalFailedStatus']); module.current(terminal_ref)
            archive = module.RUN / 'workflow/marketing-render-oom-20261017/render-status.json'
            if sha(archive) != plan['originalFailedStatus']['sha256']:
                raise ValueError('Original failed status must remain byte-identical in archive')
            module.lease_clear(plan)
            log_texts = []
            for chunk in terminal['completedChunks']:
                log_texts.append(module.current(chunk['log']).read_text())
            fresh_pairs = {}
            for chunk in plan['chunks']:
                for identity in chunk:
                    pair = original.pair(identity, config)
                    if pair is None:
                        raise ValueError('Missing fresh exact current output pair')
                    fresh_pairs[identity] = pair
            outputs = validate_terminal(plan, terminal, fresh_pairs, log_texts, module.processes())
            # Repeat source, protected91-byte, shared-budget, terminal and lease checks immediately before CAS.
            module.check(plan_path); module.current(terminal_ref); module.lease_clear(plan)
            old = read(target)
            value = {'contract': 'explicit-recovered-marketing-producer/v1', 'state': TARGET_STATE,
                     'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'qualityApproved': False,
                     'originalFailureArchive': module.ref(archive), 'originalFailureMetadata': old,
                     'newProducerTerminal': terminal_ref, 'newRenderPlan': module.ref(plan_path),
                     'newProducerPid': terminal['pid'], 'newProducerPgid': terminal['dispatcherPgid'],
                     'newControllerIdentities': [{'pid': c['controllerPid'], 'pgid': c['controllerPgid']} for c in terminal['completedChunks']],
                     'outputs': outputs, 'originalParent97249Completed': False, 'rootConditionalRelease': module.ref(release_path),
                     'manualQualityAndMetadataRefreshStillRequired': True, 'providerRequestsByReconciliation': 0}
            with intent.open('x') as f:
                json.dump({'target': str(target), 'expectedSha256': plan['originalFailedStatus']['sha256'],
                           'replacementSha256': hashlib.sha256((json.dumps(value, indent=2) + '\n').encode()).hexdigest(),
                           'release': module.ref(release_path), 'terminal': terminal_ref}, f, indent=2)
                f.flush(); os.fsync(f.fileno())
            cas_publish(target, plan['originalFailedStatus']['sha256'], value, original.save)
            original.save(evidence_dir / 'conditional-reconciliation-completion.json',
                          {'state': 'conditional_producer_status_published', 'target': module.ref(target),
                           'originalFailureArchive': module.ref(archive), 'newProducerTerminal': terminal_ref,
                           'rootRelease': module.ref(release_path), 'qualityApproved': False, 'authorityWrites': 0,
                           'sourceWrites': 0, 'budgetWrites': 0, 'providerRequests': 0})


if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--plan',type=Path,required=True)
    ap.add_argument('--release',type=Path,required=True); ap.add_argument('--release-sha256',required=True)
    args=ap.parse_args(); execute(args.plan,args.release,args.release_sha256)
