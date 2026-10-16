"""Wake the existing Codex conversation for agent reviews, separate from probes."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from discovery import atomic_json, file_hash, now_iso

THREAD = '01a12101-d3bf-7182-a5e8-c95262e34265'


def review_slot(current, deadline):
    return deadline if current >= deadline else current // 1800 * 1800


def slot_id(slot):
    return datetime.datetime.fromtimestamp(slot, datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def review_prompt(root, slot, deadline):
    final = slot == deadline
    return (
        'Scheduled agent review explicitly requested by James, in this same conversation. '
        'This is an actual agent inspection, not a script-health acknowledgement. '
        f'Review slot {slot_id(slot)}; discovery deadline 2026-10-16T18:50:02Z. '
        'Use /Users/jamesaphoenix/Desktop/projects/just-understanding-data/agent-scripts as explicit shell workdir. '
        'SSH jamesphoenix@jamess-mac-studio and inspect the tutor-incremental-20261016-recovery-10h, '
        'tutor-qualification-20261016-recovery-10h and bucks-private-tutors-20261016-recovery-10h state directories '
        'under /Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state. '
        'Read the handoff, new logs, source errors, spending, queue/Go/profile progress, qualification yield and duplicates; '
        'compare the previous agent review and investigate stalls or poor yield. Repair verified failures within '
        'the existing scope, budget and saved deadline, preserving unrelated work. No new paid allowance, '
        'SDK guard reset, Anthropic key or outreach. Do not spawn another agent or delegate this review. '
        f'Write your actual inspection time, findings and actions to {root}/reviews/{slot_id(slot)}.json on Studio '
        'with threadId, reviewedAt, findings, actions and source metrics. Only the agent writes this review acknowledgement. '
        + ('This is the final review: verify stopping and cleanup, preserve exports/caches/ledgers/unmerged source, and report final results. '
           if final else 'Keep the Studio discovery running; give James a brief factual update and finish this review turn. ')
        + 'The dispatcher will queue the next review; do not create another scheduler.'
    )


def queue_command(root, prompt):
    remote = shlex.join(['/usr/bin/env', '-u', 'ANTHROPIC_API_KEY', '-u', 'OPENAI_API_KEY', '-u', 'CODEX_API_KEY',
        'PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin', '/opt/homebrew/bin/codex', 'queue',
        '--thread', THREAD, '--message', prompt])
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(root / 'macbook-known-hosts'), 'jamesaphoenix@macbook-pro', remote]


def snapshot(root):
    campaign = root.parent
    history = campaign.parent
    value = {'observedAt': now_iso()}
    for name, path in [('discovery', campaign / 'runtime.json'),
                       ('qualification', history / 'tutor-qualification-20261016-recovery-10h/status.json'),
                       ('profiles', history / 'bucks-private-tutors-20261016-recovery-10h/status.json')]:
        state = json.loads(path.read_text())
        value[name] = {k: state.get(k) for k in ('phase', 'observedAt', 'completedQueries', 'budget', 'outcomes',
            'counts', 'decisions', 'attempts', 'queue', 'automatedQualification') if k in state}
    return value


def check(root, current=None):
    root = root.resolve()
    job = json.loads((root / 'job.json').read_text())
    authorization = root.parent / 'authorization.json'
    auth = json.loads(authorization.read_text())
    deadline = datetime.datetime.fromisoformat(auth['deadline']).timestamp()
    if (job['threadId'] != THREAD or job['intervalSeconds'] != 1800
            or file_hash(authorization) != job['authorizationSha256']
            or job['deadlineUnix'] != deadline or job['instruction'] != 'No, you should check it every 30 mins'):
        raise ValueError('The requested review thread, interval or window changed')
    current = time.time() if current is None else current
    with (root / 'dispatch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if current > deadline + 1800:
            atomic_json(root / 'finished.json', {'at': now_iso(), 'reason': 'Review delivery grace ended',
                'note': 'Delivery receipts are not completed agent inspections. Retain all missing-review evidence.'})
            retire(job)
            return
        slot = review_slot(current, deadline)
        key = slot_id(slot)
        acknowledgement = root / 'reviews' / (key + '.json')
        if acknowledgement.exists():
            review = json.loads(acknowledgement.read_text())
            if review.get('threadId') != THREAD or not review.get('reviewedAt') or not review.get('findings'):
                raise ValueError('An agent review acknowledgement is incomplete')
            if slot == deadline:
                retire(job)
            return
        receipt_path = root / 'deliveries' / (key + '.json')
        if receipt_path.exists():
            # An uncertain transport result may already have queued the prompt.
            # Never multiply model turns by retrying it blindly.
            return
        atomic_json(root / 'snapshots' / (key + '.json'), snapshot(root))
        prompt = review_prompt(root, slot, deadline)
        receipt = {'slot': key, 'threadId': THREAD, 'startedAt': now_iso(), 'status': 'submitting',
                   'inspectionComplete': False, 'targetNeedsMacBookAwake': True}
        atomic_json(receipt_path, receipt)
        try:
            response = subprocess.run(queue_command(root, prompt), capture_output=True, text=True, timeout=30)
            matched = re.fullmatch(r'Queued message ([0-9a-f-]{36}) for thread ' + re.escape(THREAD) + r'\.\s*', response.stdout)
            receipt.update(exitCode=response.returncode, stdout=response.stdout[-1500:], stderr=response.stderr[-1000:])
            if response.returncode == 0 and matched:
                receipt.update(status='queued', messageId=matched[1])
            else:
                receipt['status'] = 'delivery_unconfirmed'
        except (OSError, subprocess.TimeoutExpired) as error:
            receipt.update(status='delivery_unconfirmed', error=type(error).__name__)
        receipt['finishedAt'] = now_iso()
        atomic_json(receipt_path, receipt)
        if slot == deadline and receipt['status'] == 'queued':
            retire(job)


def retire(job):
    subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/' + job['launchdLabel']],
                   capture_output=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    check(parser.parse_args().run_dir)
