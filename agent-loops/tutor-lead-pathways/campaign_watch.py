#!/usr/bin/env python3
"""Audit identities, continue the classifier and clean only terminal task scratch."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from discovery import PaidCache, atomic_json, now_iso, parse_time
from classifier_seeds import publish as publish_classifier_seeds
from focused_run import prepare as prepare_focused
from professional_registers import replay as replay_register
from reviewed_contacts import replay as replay_public_contacts
from new_leads import classify_delta, run as publish_delta
from watch_expansion import watch as audit_progress

HERE = Path(__file__).resolve().parent


def aggregate_contacts(root, report):
    baseline = root / 'baseline/contacts.json'
    known = json.loads(baseline.read_text())
    rows = json.loads((Path(report['outputDirectory']) / 'this-stage-delta/new.json').read_text())
    register = root / 'public-source-review/bda'
    register_report = None
    if (register / 'receipt.json').exists():
        register_report = replay_register(register / 'page.html', register / 'receipt.json', baseline,
            register / 'contacts', root / 'review/candidates/bda-public-professionals.json')
        candidates = json.loads((register / 'contacts/contacts.json').read_text())
        # A register card and a newly collected directory profile may represent
        # the same tutor. Hold name collisions rather than adding both.
        matches = classify_delta(known + rows, candidates)
        rows.extend(matches['new'])
        atomic_json(register / 'contacts/current-run-needs-review.json', matches['needsReview'])
    path = root / 'exports/additional-contacts.json'
    public_reviews = root / 'review/candidates/public-contact-reviews.json'
    public_report = None
    if public_reviews.exists():
        public_output = root / 'review/public-contacts'
        public_report = replay_public_contacts(root, public_reviews, baseline, public_output)
        candidates = json.loads((public_output / 'new.json').read_text())
        matches = classify_delta(known + rows, candidates)
        rows.extend(matches['new'])
        atomic_json(public_output / 'current-run-needs-review.json', matches['needsReview'])
    atomic_json(path, rows)
    value = publish_delta(baseline, path, root / 'exports/additional-contacts')
    return {'delta': value, 'register': register_report, 'publicContacts': public_report, 'export': str(path)}


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
                previous=parent, directory_pages=4000, workers=4, baseline=Path(job['identityBaselineCsv']),
                authorized_deadline=deadline)
        atomic_json(root / 'classifier-continuation.json', {'preparedAt': now_iso(), 'runDir': str(target),
            'deadline': job['authorizedDeadline'], 'sameCache': cache, 'classificationLifetimeCapUsd': 3})
    marker = root / 'classifier-installed.json'
    if not marker.exists():
        installer = str(HERE.parents[1] / 'scripts/install-launchd-tasks.sh')
        for task in ('tutor-focused-20261013-12h', 'tutor-focused-20261013-12h-watch'):
            registered = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/com.jud.{task}'],
                                        capture_output=True, timeout=10)
            if registered.returncode == 0:
                continue
            command = ['zsh', '-f', '-c', 'source ~/.zprofile >/dev/null 2>&1 || true; exec bash "$1" --task "$2"',
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
        classifier_seeds = None
        try:
            cache = PaidCache(root, job['budgetCents'], job['priorExposureCents'])
            try:
                classifier_seeds = publish_classifier_seeds(root,
                    [Path(job['focusedParent']), Path(job['focusedContinuation'])], cache)
            finally:
                cache.db.close()
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(type(error).__name__ + ': fresh classifier seeds failed; saved sources retained')
        report = audit_progress(root, profile_root, root / 'baseline/contacts.json', root / 'baseline/contacts.json', new_only=True)
        report.update(classifier=classifier, deadline=job['authorizedDeadline'], errors=errors)
        report['classifierSeeds'] = classifier_seeds
        try:
            report['additionalContacts'] = aggregate_contacts(root, report)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(type(error).__name__ + ': additional contact audit failed; evidence retained')
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
