#!/usr/bin/env python3
"""Separate, explicitly released bounded render controllers after a proven OOM."""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

RUN = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
APP = RUN / 'marketing-revision-two/smart-cub/apps/prototypes/founder-videos'
NODE = '/opt/homebrew/opt/node@22/bin/node'
FLOOR = 50 * 1024**3


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def ref(path):
    p = Path(path)
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha(p)}


def read(path):
    return json.loads(Path(path).read_text())


def current(row):
    p = Path(row['path'])
    if p.is_symlink() or ref(p) != row:
        raise ValueError('Pinned render recovery evidence changed')
    return p


def load(path):
    spec = importlib.util.spec_from_file_location('pinned_original_successor', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def chunks(ids):
    if len(ids) != 130 or len(set(ids)) != 130 or any(',' in x for x in ids):
        raise ValueError('Exact130 unique native marketing IDs required')
    return [ids[i:i + 5] for i in range(0, 130, 5)]


def command(ids, execute=True):
    if not 1 <= len(ids) <= 5 or len(set(ids)) != len(ids):
        raise ValueError('At most5 distinct IDs per fresh native controller')
    return [NODE, '--max-old-space-size=8192', '--import', 'tsx', 'scripts/render-batch.ts',
            '--ids', ','.join(ids), '--aspect', 'both', '--jobs', '2', '--workers', '4'] + (['--execute'] if execute else [])


def dead_groups(identities, process_rows):
    owners = {int(x) for x in identities}
    if len(owners) != 2 or min(owners) <= 0:
        raise ValueError('Exact original parent/controller identities required')
    if any(pid in owners or ppid in owners or pgid in owners for pid, ppid, pgid in process_rows):
        raise ValueError('Original owner/descendant/group remains; never interrupt it')


def processes():
    return [tuple(map(int, l.split())) for l in subprocess.check_output(
        ['ps', '-axo', 'pid=,ppid=,pgid='], text=True).splitlines() if len(l.split()) == 3]


def check(path):
    p = read(path)
    if p.get('contract') != 'root-reviewed-marketing-render-recovery/v1' or p.get('app') != str(APP):
        raise ValueError('Exact isolated marketing recovery root required')
    if p.get('maximumHeapMiB') != 8192 or p.get('idsPerController') != 5 or p.get('renderJobs') != 2 \
            or p.get('workersPerRender') != 4 or p.get('nice') != 10 or p.get('freeFloorBytes') != FLOOR \
            or p.get('paidRequests') != 0 or p.get('qualityApproved') is not False or p.get('automaticRetry') is not False:
        raise ValueError('Fixed bounded rendering-only policy required')
    if sha(__file__) != p['implementationSha256']:
        raise ValueError('Exact reviewed recovery implementation required')
    for row in p['protectedFiles']:
        current(row)
    old = read(current(p['originalFailedStatus']))
    if old.get('state') != 'halted_requires_inspection_no_retry' or old.get('pid') != 97249 or old.get('renderControllerPid') != 11218:
        raise ValueError('Exact retained original OOM terminal required')
    dead_groups([97249, 11218], processes())
    original = load(current(p['originalDispatcher']))
    config, config_sha = original.load(current(p['originalConfig']))
    if original.digest(read(RUN / 'ledger/media-review-ledger.json')['budget']) != p['budgetSha256']:
        raise ValueError('Pinned shared budget changed; inspect before any new render')
    if config_sha != p['originalConfig']['sha256'] or chunks(original.identities(config)) != p['chunks']:
        raise ValueError('Native current130-ID partition changed')
    if not original.bulk_release(config_sha, original.pilot_snapshot(config), 'render'):
        raise ValueError('Exact original pilot bulk release remains mandatory')
    original.graph(config)
    for row in p['preservedOutputs']:
        current(row['output']); current(row['receipt'])
    return p, original, config


def lease_clear(plan):
    canonical = Path(subprocess.check_output(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], text=True).strip()).resolve()
    if Path(os.environ.get('TMPDIR', '')).resolve() != canonical or str(canonical) != plan['canonicalTmpdir']:
        raise ValueError('Canonical unchanged TMPDIR required')
    for parent in [Path('/private/tmp'), canonical]:
        if (parent / 'smart-cub-founder-render.lock').exists():
            raise ValueError('Both render lease namespaces must be free; no automatic cleanup')
    if shutil.disk_usage(RUN).free < FLOOR:
        raise ValueError('50GiB storage floor refuses admission')


def execute(plan_path, release_path, release_sha):
    plan, original, config = check(plan_path)
    release = read(release_path)
    if sha(release_path) != release_sha or release.get('contract') != 'root-explicit-marketing-render-recovery-release/v1' \
            or release.get('planSha256') != sha(plan_path) or release.get('allowSingleNewParent') is not True:
        raise ValueError('Separate exact Root render recovery release required')
    if socket.gethostname().split('.')[0].lower() != 'jamessmacstudio' or os.getpriority(os.PRIO_PROCESS, 0) != 10:
        raise ValueError('Studio nice10 runtime required')
    status = Path(plan['status']); status.parent.mkdir(parents=True, exist_ok=True)
    with status.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if status.exists():
            raise ValueError('Existing new recovery checkpoint; no automatic restart')
        state = {'contract': 'marketing-render-recovery-checkpoint/v1', 'plan': ref(plan_path),
                 'pid': os.getpid(), 'dispatcherPgid': os.getpgrp(), 'completedChunks': [],
                 'state': 'admitted', 'qualityApproved': False, 'automaticRetry': False, 'paidRequests': 0}
        original.save(status, state)
        try:
            for index, ids in enumerate(plan['chunks']):
                check(plan_path); lease_clear(plan)
                if Path(plan['stopPath']).exists():
                    raise ValueError('Operator stop before new controller')
                log = status.parent / f'chunk-{index:02d}.log'
                with log.open('x') as stream:
                    child = subprocess.Popen(command(ids), cwd=APP, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                    state.update(state='rendering_chunk', activeChunk=index, renderControllerPid=child.pid,
                                 renderControllerPgid=os.getpgid(child.pid))
                    original.save(status, state)
                    try:
                        while child.poll() is None:
                            if Path(plan['stopPath']).exists() or shutil.disk_usage(RUN).free < FLOOR:
                                raise ValueError('Operator stop or storage floor during owned controller')
                            time.sleep(1)
                    except BaseException:
                        if child.poll() is None:
                            child.send_signal(signal.SIGINT)
                            child.wait(timeout=180)
                        raise
                if child.returncode != 0:
                    raise ValueError('Bounded native controller failed; retain all evidence and never retry')
                check(plan_path); lease_clear(plan)
                if any(child.pid in row for row in processes()):
                    raise ValueError('Completed controller retains an owner/descendant/group')
                log_text = log.read_text()
                for prior in plan['preservedOutputs']:
                    if prior['id'].rsplit('-', 1)[0] in ids and ('SKIP verified ' + prior['id']) not in log_text.splitlines():
                        raise ValueError('Every preserved output must have an actual native verified SKIP')
                outputs = {}
                for identity in ids:
                    pair = original.pair(identity, config)
                    if pair is None:
                        raise ValueError('Native controller lacks exact atomic output pair')
                    outputs.update({identity + ':' + aspect: pair[aspect] for aspect in original.ASPECTS})
                state['completedChunks'].append({'index': index, 'ids': ids, 'log': ref(log), 'outputs': outputs,
                                                'controllerPid': child.pid, 'controllerPgid': state['renderControllerPgid']})
                state.pop('activeChunk', None); state['state'] = 'chunk_complete_needs_quality_review'
                original.save(status, state)
            all_outputs = {key: row for c in state['completedChunks'] for key, row in c['outputs'].items()}
            if len(all_outputs) != 260:
                raise ValueError('Exact260 complete outputs required')
            check(plan_path); lease_clear(plan)
            state.update(state='recovered_complete260_rendered_verified_needs_quality_review', outputs=all_outputs,
                         originalFailedStatus=plan['originalFailedStatus'], finishedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         originalFailedStatusRewritten=False)
            original.save(status, state)
        except BaseException as error:
            state.update(state='halted_requires_inspection_no_retry', failure={'type': type(error).__name__})
            original.save(status, state)
            raise


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('--plan', type=Path, required=True)
    ap.add_argument('--release', type=Path); ap.add_argument('--release-sha256'); ap.add_argument('--execute', action='store_true'); args = ap.parse_args()
    if not args.execute:
        p, _, _ = check(args.plan); print(json.dumps({'state': 'metadata_checked_no_render', 'chunks': len(p['chunks']), 'paidRequests': 0}))
    elif not args.release or not args.release_sha256:
        ap.error('Execute requires exact separate Root release')
    else:
        execute(args.plan, args.release, args.release_sha256)
