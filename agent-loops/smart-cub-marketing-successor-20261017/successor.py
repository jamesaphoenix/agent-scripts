#!/usr/bin/env python3
"""Separate successor queues: four pilot outputs, then exact root QA release before bulk."""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time

RUN = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
APP = RUN / 'marketing-revision-two/smart-cub/apps/prototypes/founder-videos'
WORK = RUN / 'workflow/marketing-successor-20261017'
PILOT = ['p9-after-a-bad-mock', 't29-ratio-as-shares']
ASPECTS = ['wide', 'tall']
NODE = '/opt/homebrew/opt/node@22/bin/node'
GUARD_SHA = '541476d01d7010d7ed68cc612bd4f7a362f4c39c5b0d63dc0e2927c68fe2b2ad'
FLOOR = 50 * 1024**3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def module(path, expected):
    require(sha(path) == expected, 'Pinned implementation changed')
    spec = importlib.util.spec_from_file_location('successor_' + Path(path).stem.replace('-', '_'), path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def ref(path):
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}


def current(row):
    path = Path(row['path'])
    require(path.is_absolute() and not path.is_symlink() and path.resolve().is_relative_to(RUN.resolve()), 'Pinned file escapes task root')
    require(path.is_file() and sha(path) == row['sha256'], 'Pinned evidence changed: ' + str(path))
    if 'bytes' in row:
        require(path.stat().st_size == row['bytes'], 'Pinned evidence size changed')
    return path


def save(path, value):
    temporary = path.with_suffix('.tmp')
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


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def predecessor(config):
    status = read(current(config['correctedTerminal']))
    require(status.get('state') == 'complete40_rendered_verified_needs_quality_review' and len(status.get('completed', [])) == 40 and status.get('qualityApproved') is False, 'All forty corrected blends must be mechanically terminal first')
    proof = read(current(config['correctedProcessProof']))
    require(status['pid'] == proof['parentPid'] and proof['hardwareChild']['pgid'] > 0, 'Actual predecessor process identities required')
    require(not alive(status['pid']), 'Corrected producer still active')
    pgid = proof['hardwareChild']['pgid']
    processes = subprocess.check_output(['ps', '-axo', 'pid=,pgid='], text=True)
    require(not any(int(line.split()[1]) == pgid for line in processes.splitlines() if len(line.split()) == 2), 'Corrected producer process group still active')
    for row in status['completed']:
        for name in ['output', 'receipt', 'mechanical']:
            current(row[name])


def identities(config):
    rows = read(APP / 'data/scripts.json')
    ids = [row['id'] for row in rows]
    require(len(ids) == len(set(ids)) == 130 and set(PILOT).issubset(ids), 'Exact130 marketing IDs including two fixed pilot IDs required')
    require(config['pilotIds'] == PILOT and config['renderJobs'] == 2 and config['workersPerRender'] == 4 and config['nice'] == 10, 'Fixed pilot/concurrency policy required')
    return ids


def load(config_path):
    require(re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) == 'jamessmacstudio', 'Mac Studio required')
    config = read(config_path)
    require(config.get('contract') == 'smart-cub-marketing-successor/v1' and config.get('app') == str(APP), 'Only the distinct successor root is supported')
    require(config.get('orchestratorSha256') == sha(Path(__file__)), 'Exact reviewed orchestrator bytes required')
    require(re.fullmatch('[0-9a-f]{64}', config.get('frozenSourceSnapshot', '')) is not None, 'Explicit final source fingerprint required')
    require(config.get('freeFloorBytes') == FLOOR and config.get('maximumMarketingReviewProcesses') == 1 and config.get('maxRequestsPerChild') == 1 and config.get('qualityApproved') is False, 'Existing bounds and advisory-only policy required')
    require(Path(config['helpers']) == RUN / 'workflow/family-helpers-58d1d149d-final20/scripts' and len(config['helperHashes']) == 26, 'Audited immutable final20 helper bundle required')
    for name, expected in config['helperHashes'].items():
        require(Path(name).name == name and sha(Path(config['helpers']) / name) == expected, 'Immutable family helper changed')
    for name, expected in config['protectedSourceHashes'].items():
        path = (APP / name).resolve()
        require(path.is_relative_to(APP.resolve()) and sha(path) == expected, 'Successor protected source changed')
    require({'scripts/render-batch.ts', 'scripts/render-batch-support.ts', 'data/scripts.json', 'data/ctas.json', 'data/cta-assignments.json'}.issubset(config['protectedSourceHashes']), 'Render and context implementation pins required')
    require(config['helperHashes']['review-media.py'] == '852264b62466688b5091667891908362a87b3b176f84a6075810cd38ba00aba8', 'Preserve exact paid review implementation identity')
    narration = read(current(config['narrationContractProof']))
    require(narration.get('status') == 'pass' and len(narration.get('checks', [])) == 149 and all(row.get('status') == 'pass' for row in narration['checks']), 'All149 narration contracts must pass')
    current(config['frozenSourceProof'])
    current(config['registrationProof'])
    current(config['cacheCopyMap'])
    current(config['advisoryValidator'])
    require(config['advisoryValidator']['sha256'] == 'eed5d447048f801cdf84742c4a29b34157a4e987bc15cd49fa78c635097c261e', 'Audited exact advisory validator required')
    require(sha(Path(config['advisoryValidator']['path']).with_name('blend-corrected-batch.py')) == 'a612882c305c6582e95d20358e238a3f3c60daab91365093eeafa05499c2044f', 'Advisory validator sibling changed')
    require(sha(RUN / 'workflow/review_admission.py') == GUARD_SHA, 'Existing shared admission guard changed')
    ledger = read(RUN / 'ledger/media-review-ledger.json')
    bound = ledger.get('production_roots', {}).get('marketing')
    require(bound and bound['root'] == str(APP) and digest(bound) == config['registeredMarketingBindingSha256'], 'Actual successor marketing authority binding required')
    require(bound['render_snapshot']['fingerprint'] == config['frozenSourceSnapshot'] and bound['historical_cache_map']['sha256'] == config['cacheCopyMap']['sha256'], 'Registered graph/cache map mismatch')
    require(not (APP / 'out').is_symlink(), 'Successor output root cannot alias old outputs')
    identities(config)
    return config, sha(config_path)


def checked(config_path, expected):
    require(sha(config_path) == expected, 'Immutable successor config changed')
    return load(config_path)


def graph(config):
    code = "import {SourceSnapshotter} from './scripts/render-batch-support.ts';console.log(new SourceSnapshotter(process.cwd()).snapshot().fingerprint)"
    actual = subprocess.check_output([NODE, '--import', 'tsx', '--input-type=module', '-e', code], cwd=APP, text=True).strip()
    require(actual == config['frozenSourceSnapshot'], 'Frozen successor graph changed')


def pair(identity, config):
    result = {}
    for aspect in ASPECTS:
        output = APP / f'out/{identity}-{aspect}.mp4'
        receipt = output.with_suffix('.render-receipt.json')
        if not output.is_file() or not receipt.is_file():
            return None
        value = read(receipt)
        require(value.get('frozenBatchSourceSnapshot') == config['frozenSourceSnapshot'] and value['output']['path'] == f'out/{identity}-{aspect}.mp4', 'Output receipt belongs to another graph/path')
        require(value['output']['sha256'] == sha(output) and value['output']['bytes'] == output.stat().st_size, 'Output receipt bytes changed')
        result[aspect] = {'output': ref(output), 'receipt': ref(receipt)}
    return result


def pilot_snapshot(config):
    outputs = {}
    for identity in PILOT:
        ready = pair(identity, config)
        require(ready is not None, 'All four exact pilot outputs required')
        for aspect in ASPECTS:
            outputs[identity + ':' + aspect] = ready[aspect]
    return outputs


def bulk_release(config_sha, outputs, role):
    path = WORK / 'pilot-qa-bulk-release.json'
    if not path.exists():
        return False
    value = read(path)
    require(value.get('contract') == 'root-marketing-successor-pilot-qa-release/v1' and value.get('configSha256') == config_sha and value.get('pilotOutputs') == outputs, 'Root bulk release must bind exact current four pilot outputs/config')
    require(value.get('pilotQualityAccepted') is True and value.get('reviewer', '').strip() and value.get('rationale', '').strip(), 'Explicit root pilot QA judgement required')
    require(value.get('allowBulkRender') is True, 'Bulk render not released')
    for row in value.get('evidence', []):
        current(row)
    require(len(value.get('evidence', [])) >= 4, 'Individual pilot QA evidence required')
    if role == 'review':
        require(value.get('allowBulkPaidReview') is True, 'Bulk paid review not released')
    return True


def wait_bulk(config_path, config_sha, config, state, status, role):
    pilot = pilot_snapshot(config)
    state.update(state='pilot_complete_waiting_root_quality_release', pilotOutputs=pilot)
    save(status, state)
    deadline = time.monotonic() + 7200
    while not bulk_release(config_sha, pilot, role):
        require(not (WORK / (role + '.STOP')).exists(), 'Operator stop; preserve checkpoint')
        require(time.monotonic() < deadline, 'Pilot QA release wait exceeded two hours; reconcile explicitly')
        require(sha(config_path) == config_sha, 'Config changed while awaiting pilot QA')
        require(pilot_snapshot(config) == pilot, 'Pilot output changed while awaiting QA')
        time.sleep(10)
    state['bulkRelease'] = ref(WORK / 'pilot-qa-bulk-release.json')
    save(status, state)


def render_command(selection):
    require(selection in [','.join(PILOT), 'marketing'], 'Only fixed pilot or full marketing selection supported')
    return [NODE, '--import', 'tsx', 'scripts/render-batch.ts', '--ids', selection, '--aspect', 'both', '--jobs', '2', '--workers', '4', '--execute']


def render(config_path, config_sha, config, state, status):
    predecessor(config)
    canonical = Path(subprocess.check_output(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], text=True).strip()).resolve()
    require(Path(os.environ.get('TMPDIR', '')).resolve() == canonical, 'Canonical Studio TMPDIR required')
    for stage, selection in [('pilot', ','.join(PILOT)), ('bulk', 'marketing')]:
        if stage == 'bulk':
            wait_bulk(config_path, config_sha, config, state, status, 'render')
        require(shutil.disk_usage(RUN).free >= FLOOR, '50GiB floor refused admission')
        for path in [Path('/private/tmp/smart-cub-founder-render.lock'), canonical / 'smart-cub-founder-render.lock']:
            require(not path.exists(), 'Render lease still occupied')
        checked(config_path, config_sha)
        graph(config)
        log = WORK / (stage + '-render.log')
        with log.open('x') as stream:
            child = subprocess.Popen(render_command(selection), cwd=APP, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            state.update(state='rendering_' + stage, renderControllerPid=child.pid, renderControllerPgid=os.getpgid(child.pid))
            save(status, state)
            try:
                while child.poll() is None:
                    require(shutil.disk_usage(RUN).free >= FLOOR, '50GiB floor reached during render')
                    require(not (WORK / 'render.STOP').exists(), 'Operator render stop')
                    time.sleep(1)
            except BaseException:
                if child.poll() is None:
                    child.send_signal(signal.SIGINT)
                    child.wait(timeout=180)
                raise
        require(child.returncode == 0, 'Owned render failed; preserve exact receipts and halt')
        if stage == 'bulk':
            text = log.read_text()
            require(all('SKIP verified ' + identity + '-' + aspect in text for identity in PILOT for aspect in ASPECTS), 'Bulk must skip the four exact pilot receipts')
        checked(config_path, config_sha)
        if stage == 'bulk':
            current(state['bulkRelease'])
        graph(config)
        for identity in PILOT if stage == 'pilot' else identities(config):
            require(pair(identity, config) is not None, 'Render finished without exact both-aspect receipts')
        state.setdefault('completedStages', []).append(stage)
        save(status, state)
    state.update(state='complete260_rendered_agent_quality_pending', qualityApproved=False)
    save(status, state)


def review_command(config, identity, aspect):
    require(identity in identities(config) and aspect in ASPECTS, 'Unregistered marketing review target')
    return [str(RUN / '.venv/bin/python'), '-B', str(Path(config['helpers']) / 'review-media.py'), 'model', '--root', str(APP), '--ids', identity, '--aspect', aspect, '--max-requests', '1', '--ledger', str(RUN / 'ledger/media-review-ledger.json'), '--evidence-root', str(APP / 'out/review-complete')]


def review(config_path, config_sha, config, state, status):
    predecessor(config)
    graph(config)
    guard = module(RUN / 'workflow/review_admission.py', GUARD_SHA)
    validator = module(Path(config['advisoryValidator']['path']), config['advisoryValidator']['sha256'])
    media = module(Path(config['helpers']) / 'review-media.py', config['helperHashes']['review-media.py'])
    ordered = PILOT + [identity for identity in identities(config) if identity not in PILOT]
    for ordinal, identity in enumerate(ordered):
        if ordinal == len(PILOT):
            wait_bulk(config_path, config_sha, config, state, status, 'review')
        for aspect in ASPECTS:
            deadline = time.monotonic() + 7200
            ready = pair(identity, config)
            while ready is None:
                require(time.monotonic() < deadline and not (WORK / 'review.STOP').exists(), 'Review wait stopped/expired')
                producer = WORK / 'render-status.json'
                require(not producer.exists() or read(producer)['state'] != 'halted_requires_inspection_no_retry', 'Successor renderer halted')
                time.sleep(10)
                ready = pair(identity, config)
            checked(config_path, config_sha)
            producer = WORK / 'render-status.json'
            require(producer.exists() and read(producer)['state'] != 'halted_requires_inspection_no_retry', 'Healthy exact successor producer required before review admission')
            if ordinal >= len(PILOT):
                current(state['bulkRelease'])
            intent = {'id': identity, 'aspect': aspect, 'outputSha256': ready[aspect]['output']['sha256']}
            cache = APP / 'out/review-complete' / identity / aspect
            for path in cache.glob('*/request-*.json'):
                old = read(path)
                require(old.get('state') == 'completed_advisory' or not (old.get('reservation_id') or old.get('state') == 'submitted_unknown'), 'Prior paid intent unresolved; no resubmission')
            log = WORK / ('review-' + identity + '-' + aspect + '.log')
            state.update(state='waiting_marketing_guard', activeIntent=intent, activeLog=str(log))
            save(status, state)
            with guard.admission(RUN, family='marketing', intent=intent, stop_path=WORK / 'review.STOP') as lease:
                checked(config_path, config_sha)
                require(pair(identity, config) == ready, 'Pair changed while waiting admission')
                with log.open('x') as stream:
                    state['state'] = 'launch_intent_saved_before_child'
                    save(status, state)
                    child = lease.popen(review_command(config, identity, aspect), cwd=APP, stdout=stream, stderr=subprocess.STDOUT, env={**os.environ, 'PYTHONUNBUFFERED': '1'})
                    state.update(state='reviewing', activeReviewPid=child.pid)
                    save(status, state)
                    code = child.wait()
            require(code == 0, 'Review child failed; reconcile saved intent without retry')
            checked(config_path, config_sha)
            results = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
            selected = [row for row in results if row.get('id') == identity and row.get('aspect') == aspect]
            require(len(selected) == 1, 'Exactly one saved observation result required')
            result = selected[0]
            request = Path(result['evidence'])
            require(request.parent.resolve() == (cache / intent['outputSha256'][:20]).resolve() and not request.is_symlink() and request.name.startswith('request-'), 'Saved job outside exact output cache')
            job = read(request)
            item = media.inventory(APP, (APP / '../../web/content/skill-videos/published.json').resolve())[identity]
            item = {**item, 'outputSha256': intent['outputSha256']}
            complete = validator.observation(result, job, item, aspect, media, config['helperHashes']['review-media.py'])
            require(pair(identity, config) == ready, 'Reviewed output changed during observation')
            state['completed'].append({'id': identity, 'aspect': aspect, 'pair': ready[aspect], 'request': ref(request), 'log': ref(log), 'result': result, 'observedComplete': complete, 'qualityApproved': False})
            for key in ['activeIntent', 'activeLog', 'activeReviewPid']:
                state.pop(key, None)
            state['state'] = 'advisory_saved_agent_judgement_pending' if complete else 'halted_incomplete_observation_no_retry'
            save(status, state)
            if not complete:
                return
    state.update(state='complete260_advisories_agent_judgements_pending', qualityApproved=False)
    save(status, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--mode', required=True, choices=['render', 'review'])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    config, config_sha = load(args.config)
    if args.check:
        print(json.dumps({'state': 'metadata_verified_no_media_or_provider_call', 'mode': args.mode, 'configSha256': config_sha, 'pilotIds': PILOT, 'bulkRequiresSeparateRootPilotQaRelease': True}))
        return
    require(os.getpriority(os.PRIO_PROCESS, 0) == 10, 'Exact nice10 policy required')
    WORK.mkdir(parents=True, exist_ok=True)
    status = WORK / (args.mode + '-status.json')
    with status.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not status.exists(), 'Existing successor checkpoint requires explicit reconciliation, no automatic restart')
        state = {'contract': 'marketing-successor-' + args.mode + '/v1', 'configSha256': config_sha, 'state': 'admitted', 'pid': os.getpid(), 'dispatcherPgid': os.getpgrp(), 'createdAt': now(), 'completed': [], 'qualityApproved': False, 'automaticRetry': False}
        save(status, state)
        try:
            (render if args.mode == 'render' else review)(args.config, config_sha, config, state, status)
        except BaseException as error:
            state.update(state='halted_requires_inspection_no_retry', failure={'type': type(error).__name__}, haltedAt=now())
            save(status, state)
            raise


if __name__ == '__main__':
    main()
