#!/usr/bin/env python3
"""Activate an explicitly authorised extra window after its native owners seal."""
import argparse
import contextlib
import datetime
import fcntl
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time

from configuration import campaign_window, loaded_source_sha
from discovery import atomic_json, file_hash, now_iso
from prepare_campaign import archive_history, prepare
from campaign_watch import watch as audit_parent

HERE = Path(__file__).resolve().parent


def validate_request(request, parent, child):
    job = json.loads((parent / 'manifest.json').read_text())
    campaign_window(job)
    if (request.get('parentManifestSha256') != file_hash(parent / 'manifest.json')
            or request.get('parentCampaign') != str(parent.resolve())
            or request.get('requestedAt') != job['authorizedDeadline']
            or request.get('campaignBudgetCapUsd') != 100
            or request.get('reservedDecisionsLifetimeCents') != 300
            or request.get('sdkTokenCeiling') != job['sdkTokenCeiling']
            or request.get('researchPriority') != '11_plus_and_private_academic'
            or request.get('newPublicPageAllowance') != 12000
            or request.get('runId') != child.name
            or child.parent.resolve() != parent.parent.resolve()
            or child == parent
            or not re.fullmatch(r'tutor-lead-pathways-[a-z0-9]+(?:-[a-z0-9]+)*', child.name)
            or not re.fullmatch(r'bucks-private-tutors-[a-z0-9]+(?:-[a-z0-9]+)*', request.get('profileRunId', ''))):
        raise ValueError('Extension request changed its parent, scope or lifetime limits')
    start, deadline, _ = campaign_window({'hours': 12, 'authorizedStartedAt': request['requestedAt'],
        'authorizedDeadline': request['deadline']})
    observed = datetime.datetime.fromisoformat(request['userRequestedAt'])
    if observed.tzinfo is None or observed > start:
        raise ValueError('Extension requires an explicit dated user request before activation')
    return job, start.timestamp(), deadline.timestamp()


def owners_sealed(parent, profile):
    if json.loads((parent / 'runtime.json').read_text()).get('phase') != 'complete':
        return False
    if json.loads((profile / 'status.json').read_text()).get('phase') != 'stage_complete':
        return False
    for path in (parent / 'run.lock', profile / 'owner.lock'):
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
    return True


def extension_authorization(request, parent, history, request_sha):
    """The parent's sealed exposure already contains the shared USD3 reserve."""
    job = json.loads((parent / 'manifest.json').read_text())
    exposure, _ = archive_history(history)
    if (job['sharedCapCents'] != 10000 or job['reservedDecisionsLifetimeCents'] != 300
            or job['priorExposureCents'] != job['priorDfsExposureCents'] + 300
            or not 300 <= exposure <= 10000):
        raise ValueError('Parent does not prove the existing shared reserve')
    remaining = 10000 - exposure
    return dict(request, priorDfsExposureCents=exposure - 300,
        maximumNewDfsCents=remaining if remaining >= 100 else 0,
        unallocatedBelowReservationCents=remaining if remaining < 100 else 0,
        extensionRequestSha256=request_sha)


def check_preparation(child, request_sha):
    proof = json.loads((child / 'preparation-complete.json').read_text())
    manifest = json.loads((child / 'manifest.json').read_text())
    if (proof['extensionRequestSha256'] != request_sha
            or proof['manifestSha256'] != file_hash(child / 'manifest.json')
            or proof['authorizationSha256'] != file_hash(child / 'authorization.json')
            or proof['profileJobSha256'] != file_hash(Path(manifest['profileRunDir']) / 'job.json')
            or manifest['planSha256'] != file_hash(child / 'plan.json')):
        raise ValueError('Prepared extension changed before activation')
    return manifest


def install_registered(child, manifest):
    source = HERE.parents[1]
    installer = source / 'scripts/install-launchd-tasks.sh'
    registry = source / 'launchd-tasks/registry.json'
    tasks = [child.name, child.name + '-watch', Path(manifest['profileRunDir']).name,
        Path(manifest['profileRunDir']).name + '-watch', child.name + '-campaign-watch']
    entries = {entry['id']: entry for entry in json.loads(registry.read_text())['tasks']}
    for task in tasks:
        if task not in entries or entries[task]['machine'] != 'mac-studio' or not entries[task]['enabled']:
            raise ValueError('Extension owner or monitor is not registered on Studio')
        label = entries[task]['launchdLabel']
        loaded = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{label}'],
            capture_output=True, timeout=10)
        if loaded.returncode == 0:
            continue
        environment = dict(os.environ, AGENT_SCRIPTS_LAUNCHD_TASK_REGISTRY=str(registry))
        subprocess.run(['zsh', '-f', '-c',
            'source ~/.zprofile >/dev/null 2>&1 || true; exec bash "$1" --task "$2"',
            'tutor-extension-installer', str(installer), task], env=environment,
            check=True, capture_output=True, timeout=120)
    return tasks


def watch(child, request_sha, label, clock=time.time, installer=install_registered):
    request_path = child / 'extension-request.json'
    if file_hash(request_path) != request_sha:
        raise ValueError('Pinned extension request changed')
    request = json.loads(request_path.read_text())
    parent = Path(request['parentCampaign'])
    parent_job, start, deadline = validate_request(request, parent, child)
    with (child / 'extension-watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'phase': 'another_extension_check_running'}
        current = clock()
        if current < start:
            result = {'phase': 'waiting_for_original_deadline', 'observedAt': now_iso(),
                'startsAt': request['requestedAt'], 'deadline': request['deadline'], 'paidCalls': 0}
        elif current >= deadline:
            result = {'phase': 'extension_deadline_reached', 'observedAt': now_iso(), 'paidCalls': 0}
        elif not owners_sealed(parent, Path(parent_job['profileRunDir'])):
            result = {'phase': 'waiting_for_parent_owners_to_seal', 'observedAt': now_iso(), 'paidCalls': 0}
        else:
            if not (child / 'preparation-complete.json').exists():
                if (child / 'manifest.json').exists():
                    raise ValueError('Partial preparation held; preserve its caches for review')
                # Freeze and verify the final contact export before inheriting it.
                with contextlib.redirect_stdout(io.StringIO()):
                    audit_parent(parent, 'com.jud.' + parent.name + '-campaign-watch')
                with (parent / 'campaign-watch.lock').open('a') as parent_lock:
                    fcntl.flock(parent_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    auth = extension_authorization(request, parent, parent.parent, request_sha)
                    atomic_json(child / 'authorization.json', auth)
                    focused = Path(parent_job['focusedParent'])
                    prepare(child, child / 'authorization.json', parent.parent,
                        Path(parent_job['profileRunDir']), child.parent / request['profileRunId'],
                        Path(parent_job['identityBaselineCsv']).with_suffix('.json'), focused,
                        parent / 'towns.json', Path(parent_job['codexBinary']),
                        Path(parent_job['crawlerBinary']), extension_parent=parent)
            manifest = check_preparation(child, request_sha)
            tasks = installer(child, manifest)
            result = {'phase': 'activated', 'observedAt': now_iso(), 'runId': child.name,
                'sourceSha': loaded_source_sha(), 'startsAt': request['requestedAt'], 'deadline': request['deadline'],
                'registeredTasks': tasks, 'newDataForSeoCeilingCents': manifest['budgetCents'],
                'inheritedSharedExposureCents': manifest['priorExposureCents'],
                'sharedCapCents': 10000, 'sdkTokenCeiling': manifest['sdkTokenCeiling'],
                'classifier': manifest['classifierContinuation'], 'paidCallsDuringPreparation': 0}
            atomic_json(child / 'activation.json', result)
        result['requestSha256'] = request_sha
        atomic_json(child / 'extension-status.json', result)
        if result['phase'] in ('activated', 'extension_deadline_reached'):
            subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{label}'], capture_output=True, timeout=10)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--request-sha256', required=True)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    if args.label != 'com.jud.' + args.run_dir.name + '-extension-watch':
        parser.error('Extension watcher must address its own registered stage')
    try:
        print(json.dumps(watch(args.run_dir, args.request_sha256, args.label)))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        atomic_json(args.run_dir / 'extension-status.json', {'phase': 'activation_failed',
            'observedAt': now_iso(), 'errorType': type(error).__name__,
            'note': 'Evidence and lifetime limits preserved; retry at the next registered check.'})
        raise SystemExit('Extension activation failed; inspect native evidence without resetting its limits') from None
