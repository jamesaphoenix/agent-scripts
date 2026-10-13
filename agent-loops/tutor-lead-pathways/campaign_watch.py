#!/usr/bin/env python3
"""Audit identities, continue the classifier and clean only terminal task scratch."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from discovery import atomic_json, now_iso, parse_time
from focused_run import prepare as prepare_focused
from watch_expansion import watch as audit_progress

HERE = Path(__file__).resolve().parent


def continue_classifier(root, job):
    parent, target = Path(job['focusedParent']), Path(job['focusedContinuation'])
    if time.time() >= parse_time(job['authorizedDeadline']).timestamp():
        return 'deadline_reached'
    if not target.exists():
        status = json.loads((parent / 'status.json').read_text())
        if status['status'] != 'complete':
            return 'previous_classifier_running_or_held'
        cache = json.loads((parent / 'job.json').read_text())['cache']
        deadline = parse_time(job['authorizedDeadline']).timestamp()
        source_sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=HERE.parents[1],
                                    capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        with (parent / 'run.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'previous_classifier_finalizing'
            prepare_focused(target, Path(job['keywordIndexPath']), Path(cache), Path(job['crawlerBinary']),
                Path(job['focusedHistory']), (deadline - time.time()) / 3600, 10000, 2000, source_sha,
                previous=parent, directory_pages=2000, workers=4, baseline=Path(job['identityBaselineCsv']),
                authorized_deadline=deadline)
        atomic_json(root / 'classifier-continuation.json', {'preparedAt': now_iso(), 'runDir': str(target),
            'deadline': job['authorizedDeadline'], 'sameCache': cache, 'classificationLifetimeCapUsd': 3})
    marker = root / 'classifier-installed.json'
    if not marker.exists():
        installer = str(HERE.parents[1] / 'scripts/install-launchd-tasks.sh')
        for task in ('tutor-focused-20261013-12h', 'tutor-focused-20261013-12h-watch'):
            command = ['zsh', '-c', 'source ~/.zprofile >/dev/null 2>&1 || true; exec bash "$1" --task "$2"',
                       'tutor-classifier-installer', installer, task]
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        atomic_json(marker, {'installedAt': now_iso(), 'runDir': str(target)})
    return json.loads((target / 'status.json').read_text())['status']


def terminal_cleanup(root, profile_root, job):
    runtime = json.loads((root / 'runtime.json').read_text())
    profiles = json.loads((profile_root / 'status.json').read_text())
    if runtime.get('phase') != 'complete' or profiles.get('phase') != 'stage_complete':
        return False
    target = Path(job['focusedContinuation'])
    if target.exists() and json.loads((target / 'status.json').read_text())['status'] in ('prepared', 'running', 'interrupted'):
        return False
    removed = []
    for relative in ('sdk/ssh-known-hosts', 'crawls/seed-candidates.txt'):
        path = root / relative
        if path.is_file() and not path.is_symlink():
            path.unlink()
            removed.append(str(path))
    atomic_json(root / 'cleanup.json', {'completedAt': now_iso(), 'removed': removed,
        'retained': [str(root / 'exports'), str(root / 'receipts'), str(root / 'paid.sqlite'),
            str(root / 'baseline'), str(root / 'metrics'), str(root / 'sdk/results'),
            str(profile_root), str(HERE.parents[1])],
        'note': 'Evidence, reusable caches, budget ledgers and source awaiting PR review are retained. No merged PR worktree was deleted.'})
    return True


def watch(root, label):
    job = json.loads((root / 'manifest.json').read_text())
    profile_root = Path(job['profileRunDir'])
    with (root / 'campaign-watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        errors = []
        try:
            classifier = continue_classifier(root, job)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            classifier = 'continuation_failed'
            errors.append(type(error).__name__ + ': classifier continuation failed; inspect native logs')
        report = audit_progress(root, profile_root, root / 'baseline/contacts.json', root / 'baseline/contacts.json')
        report.update(classifier=classifier, deadline=job['authorizedDeadline'], errors=errors)
        report['cleaned'] = terminal_cleanup(root, profile_root, job)
        atomic_json(root / 'campaign-status.json', report)
        print(json.dumps({k: report[k] for k in ('observedAt', 'thisStageDelta', 'classifier', 'deadline', 'errors', 'cleaned')}))
        if report['cleaned']:
            subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{label}'], capture_output=True, timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    if args.label != 'com.jud.' + args.run_dir.name + '-campaign-watch':
        parser.error('Monitor label must match its prepared campaign')
    watch(args.run_dir, args.label)
