#!/usr/bin/env python3
"""Argument-only launcher; absent approved binding refuses even inactive installation."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys

RUN = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
PYTHON = RUN / '.venv/bin/python'
CONFIG = Path(__file__).with_name('approved-review-batch.json')


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            result.update(block)
    return result.hexdigest()


def pinned(row):
    if set(row) != {'path', 'bytes', 'sha256'}:
        raise ValueError('Exact pinned file reference required')
    path = Path(row['path'])
    if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(RUN.resolve()):
        raise ValueError('Pinned path outside task run root')
    if not path.is_file() or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
        raise ValueError('Pinned bytes changed: ' + str(path))
    return path


def command():
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise ValueError('Mac Studio required')
    value = json.loads(CONFIG.read_text())
    if set(value) != {'contract', 'script', 'plan', 'status', 'stopPath'} or value['contract'] != 'smart-cub-corrected40-review-launch/v1':
        raise ValueError('Exact approved advisory launch binding required')
    script, plan = pinned(value['script']), pinned(value['plan'])
    status, stop = Path(value['status']), Path(value['stopPath'])
    if script.name != 'review-corrected-batch.py':
        raise ValueError('Only the reviewed corrected advisory implementation is supported')
    if any(not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(RUN.resolve()) for path in [status, stop]):
        raise ValueError('Status and stop must remain under task run root')
    if status == stop or status in {script, plan} or stop in {script, plan}:
        raise ValueError('Distinct status and stop paths required')
    if status.exists():
        raise ValueError('Existing review status requires explicit reconciliation; no automatic paid restart')
    return [str(PYTHON), '-B', str(script), 'run', '--plan', str(plan), '--plan-sha256', value['plan']['sha256'], '--status', str(status), '--stop-path', str(stop), '--execute']


def main():
    argv = command()
    if sys.argv[1:] == ['--check']:
        print(json.dumps({'state': 'pinned_advisory_launcher_verified_no_provider_call', 'argv': argv}))
        return
    if sys.argv[1:] != ['--execute']:
        raise ValueError('Explicit --execute or metadata-only --check required')
    if os.getpriority(os.PRIO_PROCESS, 0) != 10:
        raise ValueError('Exact nice10 policy required')
    os.execv(argv[0], argv)


if __name__ == '__main__':
    main()
