#!/usr/bin/env python3
"""Explicit new checkpoints after two proven credential-preflight failures only."""
import argparse
import datetime
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

RUN = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
GUARD_SHA = '541476d01d7010d7ed68cc612bd4f7a362f4c39c5b0d63dc0e2927c68fe2b2ad'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path):
    path = Path(path)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}


def current(row):
    path = Path(row['path'])
    if path.is_symlink() or ref(path) != row:
        raise ValueError('Pinned recovery evidence changed')
    return path


def read(path):
    return json.loads(Path(path).read_text())


def load(path):
    spec = importlib.util.spec_from_file_location('recovery_' + Path(path).stem.replace('-', '_'), path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def completed_keys(rows):
    keys = [(row['id'], row['aspect']) for row in rows]
    if len(keys) != len(set(keys)) or any(row.get('observedComplete') is not True for row in rows):
        raise ValueError('Prior completions must be unique and completely observed')
    return set(keys)


def no_submission(proof, intent):
    expected = {'identity': intent['id'], 'aspect': intent['aspect'], 'outputSha256': intent['outputSha256'],
                'requestCreatedByThisAttempt': False, 'providerResponseForThisAttempt': None,
                'safeError': '1Password Gemini key read failed; no paid request submitted'}
    if any(proof.get(k) != v for k, v in expected.items()):
        raise ValueError('Only the exact proven no-submission preflight intent is admissible')


def refuse_uncertain(cache):
    for path in cache.glob('*/request-*.json'):
        job = read(path)
        if job.get('state') != 'completed_advisory' and (job.get('reservation_id') or job.get('state') == 'submitted_unknown'):
            raise ValueError('Unresolved paid intent; never retry')


def memory_key(config):
    # One actual CLI read per new parent lifetime, never one read per media item.
    result = subprocess.run(['/opt/homebrew/bin/op', '--config', str(config), '--cache=false',
                             'read', 'op://api-keys/Gemini/api_key'], capture_output=True, text=True,
                            timeout=25, env={**os.environ, 'OP_BIOMETRIC_UNLOCK_ENABLED': 'false',
                                             'OP_LOAD_DESKTOP_APP_SETTINGS': 'false'})
    if result.returncode or not result.stdout.strip():
        raise ValueError('Credential preflight failed; no provider child started')
    return result.stdout.strip()


def budget_binding(value, protected):
    budget = value['budget']
    if budget.get('currency') != 'USD' or budget.get('cap_cents') != 10000:
        raise ValueError('Original shared USD100 authority required')
    if any(budget['reservations'].get(key) != row for key, row in protected.items()):
        raise ValueError('Protected original settled reservation changed')


def credential_delay(config, at=None):
    target = datetime.datetime.fromisoformat(config['credentialReadNotBeforeUtc'])
    if target.tzinfo is None or target.utcoffset() != datetime.timedelta(0):
        raise ValueError('Explicit UTC credential not-before gate required')
    return max(0, (target - (at or datetime.datetime.now(datetime.timezone.utc))).total_seconds())


def check(config_path):
    config = read(config_path)
    if config.get('contract') != 'smart-cub-explicit-review-recovery/v1' or config.get('family') not in {'marketing', 'corrected'}:
        raise ValueError('Exact fixed-family recovery configuration required')
    if config.get('maximumChildren') != 1 or config.get('qualityApproved') is not False or config.get('automaticRetry') is not False:
        raise ValueError('Serial advisory-only, no automatic retry policy required')
    credential_delay(config)
    for entry in config['protectedFiles']:
        path = current(entry)
        if not path.is_absolute() or not path.resolve().is_relative_to(RUN.resolve()):
            raise ValueError('Recovery evidence must stay within the task run')
    for key in ['dispatcher', 'legacyInput', 'legacyStatus', 'failureProof', 'guard', 'shim']:
        current(config[key])
    budget_binding(read(RUN / 'ledger/media-review-ledger.json'), config['protectedSettledReservations'])
    if config['guard']['sha256'] != GUARD_SHA or sha(RUN / 'workflow/review_admission.py') != GUARD_SHA:
        raise ValueError('Shared admission guard must remain unchanged')
    shim_command = Path(config['shim']['path']).parent / 'bin/op'
    if shim_command.is_symlink() or not shim_command.is_file() or sha(shim_command) != config['shim']['sha256']:
        raise ValueError('Exact isolated child credential shim required')
    old = read(config['legacyStatus']['path'])
    expected_count = {'marketing': 27, 'corrected': 36}[config['family']]
    if old.get('state') != 'halted_requires_inspection_no_retry' or len(old['completed']) != expected_count:
        raise ValueError('Only exact reviewed terminal checkpoints can be migrated')
    completed_keys(old['completed'])
    failures = read(config['failureProof']['path'])['rows']
    failure = next(row for row in failures if row['family'] == config['family'])
    no_submission(failure, old['activeIntent'])
    current(failure['log'])
    for pid in [old['pid'], old.get('activeReviewPid', old.get('childPid'))]:
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError('Actual failed owner identities required')
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise ValueError('Previous reviewer still alive or PID reused; inspect before admission')
    for name in ['status', 'stopPath', 'credentialConfig']:
        p = Path(config[name])
        if not p.is_absolute() or p.is_symlink() or not p.resolve().is_relative_to(RUN.resolve()):
            raise ValueError('Distinct task-owned recovery paths required')
    if Path(config['status']) == Path(config['legacyStatus']['path']) or Path(config['status']) == Path(config['stopPath']):
        raise ValueError('Preserve old terminal checkpoint and use a distinct stop path')
    return config, old


class Adapter:
    def __init__(self, config):
        self.config = config
        self.family = config['family']
        self.original = load(current(config['dispatcher']))
        if self.family == 'marketing':
            self.value, self.value_sha = self.original.load(Path(config['legacyInput']['path']))
            self.app = self.original.APP
            self.media = self.original.module(Path(self.value['helpers']) / 'review-media.py', self.value['helperHashes']['review-media.py'])
            self.validator = self.original.module(Path(self.value['advisoryValidator']['path']), self.value['advisoryValidator']['sha256'])
            ordered = self.original.PILOT + [x for x in self.original.identities(self.value) if x not in self.original.PILOT]
            self.jobs = [{'id': x, 'aspect': aspect} for x in ordered for aspect in self.original.ASPECTS]
            if not self.original.bulk_release(self.value_sha, self.original.pilot_snapshot(self.value), 'review'):
                raise ValueError('The existing exact Root bulk QA gate is still required')
        else:
            self.value = read(config['legacyInput']['path'])
            self.blend, self.correction = self.original.stable(self.value)
            self.app = Path(self.blend['root'])
            self.review = self.original.batch.module(Path(self.blend['helpers']) / 'review-corrected-teaching.py')
            self.media = self.review.media
            self.validator = self.original
            self.jobs = self.value['jobs']

    def ready(self, row):
        if self.family == 'marketing':
            pair = self.original.pair(row['id'], self.value)
            return pair[row['aspect']] if pair else None
        self.correction.preflight_output(self.app, Path(self.blend['ledger']), row['id'], row['aspect'])
        return {'output': row['output'], 'receipt': row['receipt']}

    def intent(self, row, ready):
        identity = row['id'] if self.family == 'marketing' else 'teaching:' + row['id']
        return {'id': identity, 'aspect': row['aspect'], 'outputSha256': ready['output']['sha256']}

    def fresh(self, row, ready):
        check(Path(self.config['configPath']))
        if self.family == 'marketing':
            self.original.checked(Path(self.config['legacyInput']['path']), self.value_sha)
            self.original.graph(self.value)
            producer = read(self.original.WORK / 'render-status.json')
            if producer['state'] == 'halted_requires_inspection_no_retry':
                raise ValueError('Marketing producer halted')
            self.original.current(self.config['bulkRelease'])
        else:
            self.original.stable(self.value)
            self.review.refuse_uncertain_prior(self.app, 'teaching:' + row['id'], row['aspect'])
        if self.ready(row) != ready:
            raise ValueError('Exact output changed before submission')

    def command(self, row):
        return self.original.review_command(self.value, row['id'], row['aspect']) if self.family == 'marketing' else self.original.command(self.value, self.blend, row)

    def record(self, row, ready, result, request, log):
        intent = self.intent(row, ready)
        cache = self.app / 'out/review-complete' / intent['id'].replace(':', '_') / row['aspect']
        if request.is_symlink() or request.parent.resolve() != (cache / intent['outputSha256'][:20]).resolve():
            raise ValueError('Current exact request/output cache required')
        job = read(request)
        if self.family == 'marketing':
            item = self.media.inventory(self.app, (self.app / '../../web/content/skill-videos/published.json').resolve())[row['id']]
            implementation = self.value['helperHashes']['review-media.py']
        else:
            item = self.review.inventory(self.app, Path(self.blend['ledger']), True)[intent['id']]
            implementation = sha(Path(self.blend['helpers']) / 'review-media.py')
        item = {**item, 'outputSha256': intent['outputSha256']}
        complete = self.validator.observation(result, job, item, row['aspect'], self.media, implementation)
        value = {'id': row['id'], 'aspect': row['aspect'], 'request': ref(request), 'log': ref(log),
                 'result': result, 'observedComplete': complete, 'qualityApproved': False}
        if self.family == 'marketing':
            value['pair'] = ready
        else:
            value.update(outputSha256=intent['outputSha256'], agentJudgement='pending')
        return value


def execute(config_path, release_path, release_sha):
    config, old = check(config_path)
    release = read(release_path)
    if sha(release_path) != release_sha or release.get('contract') != 'root-explicit-review-recovery-release/v1' \
            or release.get('configSha256') != sha(config_path) or release.get('family') != config['family'] \
            or release.get('allowSingleNewParent') is not True:
        raise ValueError('Separate exact Root recovery release required')
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio' or os.getpriority(os.PRIO_PROCESS, 0) != 10:
        raise ValueError('Studio nice10 runtime required')
    status = Path(config['status'])
    status.parent.mkdir(parents=True, exist_ok=True)
    with status.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if status.exists():
            raise ValueError('Existing recovery checkpoint; no automatic restart')
        adapter = Adapter({**config, 'configPath': str(config_path)})
        inherited = completed_keys(old['completed'])
        rows = {(x['id'], x['aspect']): x for x in adapter.jobs}
        if not inherited <= rows.keys():
            raise ValueError('Prior completions outside the fixed current scope')
        for saved in old['completed']:
            row = rows[saved['id'], saved['aspect']]
            current(saved['request']); current(saved['log'])
            rebuilt = adapter.record(row, adapter.ready(row), saved['result'], Path(saved['request']['path']), Path(saved['log']['path']))
            if rebuilt != saved:
                raise ValueError('Prior exact completion binding changed')
        guard = load(current(config['guard']))
        state = {'contract': 'explicit-review-recovery-checkpoint/v1', 'family': config['family'], 'config': ref(config_path),
                 'legacyStatus': config['legacyStatus'], 'pid': os.getpid(), 'dispatcherPgid': os.getpgrp(),
                 'state': 'admitted', 'inheritedCompleted': old['completed'], 'completed': [],
                 'qualityApproved': False, 'automaticRetry': False}
        save = adapter.original.save if config['family'] == 'marketing' else adapter.correction.blend.atomic_json
        save(status, state)
        secret, env = None, {}
        try:
            while credential_delay(config) > 0:
                state.update(state='waiting_for_credential_not_before_gate',
                             credentialReadNotBeforeUtc=config['credentialReadNotBeforeUtc'])
                save(status, state)
                if Path(config['stopPath']).exists() or ref(config_path) != state['config']:
                    raise ValueError('Operator stop or changed config while awaiting credential window')
                current(config['legacyStatus'])
                time.sleep(min(30, credential_delay(config)))
            check(config_path)
            # No secret is loaded until every prior completion and metadata pin passes.
            # Persist the new preflight checkpoint first, including a possible key-read failure.
            secret = memory_key(Path(config['credentialConfig']))
            env = {**os.environ, 'PATH': str(Path(config['shim']['path']).parent / 'bin') + ':' + os.environ.get('PATH', ''),
                   'SMART_CUB_REVIEW_GEMINI_KEY': secret, 'PYTHONUNBUFFERED': '1'}
            for row in adapter.jobs:
                key = row['id'], row['aspect']
                if key in inherited:
                    continue
                deadline = time.monotonic() + 7200
                ready = adapter.ready(row)
                while ready is None:
                    if Path(config['stopPath']).exists() or time.monotonic() > deadline:
                        raise ValueError('Operator stop or bounded output wait expired')
                    time.sleep(10)
                    ready = adapter.ready(row)
                intent = adapter.intent(row, ready)
                cache = adapter.app / 'out/review-complete' / intent['id'].replace(':', '_') / row['aspect']
                refuse_uncertain(cache)
                log = status.parent / (status.stem + '-' + row['id'] + '-' + row['aspect'] + '.log')
                state.update(state='waiting_for_guard', activeIntent=intent, activeLog=str(log))
                save(status, state)
                with guard.admission(RUN, family=config['family'], intent=intent, stop_path=Path(config['stopPath'])) as lease:
                    adapter.fresh(row, ready)
                    refuse_uncertain(cache)
                    with log.open('x') as stream:
                        state['state'] = 'launch_intent_saved_before_child'
                        save(status, state)
                        child = lease.popen(adapter.command(row), cwd=adapter.app, stdout=stream, stderr=stream, env=env)
                        state.update(state='reviewing', childPid=child.pid)
                        save(status, state)
                        code = child.wait()
                adapter.fresh(row, ready)
                if code:
                    raise ValueError('Review child failed; preserve intent and never automatically retry')
                results = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
                selected = [x for x in results if x.get('id') == intent['id'] and x.get('aspect') == row['aspect']]
                if len(selected) != 1:
                    raise ValueError('One exact completed advisory result required')
                result = selected[0]
                record = adapter.record(row, ready, result, Path(result['evidence']), log)
                state['completed'].append(record)
                for name in ['activeIntent', 'activeLog', 'childPid']:
                    state.pop(name, None)
                if not record['observedComplete']:
                    state['state'] = 'halted_incomplete_observation_no_retry'
                    save(status, state)
                    return
                state['state'] = 'advisory_saved_agent_judgement_pending'
                save(status, state)
            state['state'] = 'complete_advisories_agent_judgements_pending'
            save(status, state)
        except BaseException as error:
            state.update(state='halted_requires_inspection_no_retry', failure={'type': type(error).__name__})
            save(status, state)
            raise
        finally:
            env.pop('SMART_CUB_REVIEW_GEMINI_KEY', None)
            secret = None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--release', type=Path)
    parser.add_argument('--release-sha256')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        value, old = check(args.config)
        print(json.dumps({'state': 'metadata_checked_no_credential_or_provider_call', 'family': value['family'], 'inheritedCompletions': len(old['completed'])}))
    elif not args.release or not args.release_sha256:
        parser.error('Execute needs the exact separate Root release')
    else:
        execute(args.config, args.release, args.release_sha256)
