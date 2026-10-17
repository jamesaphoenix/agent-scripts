#!/usr/bin/env python3
"""One-shot isolated correction renders, with pinned staged inputs and no paid APIs."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

RUN = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
APP = RUN / 'smart-cub/apps/prototypes/founder-videos'
PROJECT = RUN / 'teaching-repairs/style-lab'
MANIFEST = RUN / 'teaching-repairs/source-patches.json'
MANIFEST_SHA = '72697483d6cc9e71a70f8cecdd60cda9389b81c23ddcc5c61c59045934d25be8'
RENDERER_SHA = '35ef2c57bc93e13b63f58796e08d9281c2c4409b4983169ace24837cf5d46ce1'
STATUS = RUN / 'workflow/teaching-corrections-dispatch-status.json'
NODE = '/opt/homebrew/opt/node@22/bin/node'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(value):
    temporary = STATUS.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, STATUS)


def preflight():
    assert re.sub(r'[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) == 'jamessmacstudio'
    assert sha(MANIFEST) == MANIFEST_SHA, 'Staged patch manifest changed'
    assert sha(APP / 'scripts/render-teaching-corrections.ts') == RENDERER_SHA, 'Renderer changed'
    data = json.loads(MANIFEST.read_text())
    assert data['project'] == str(PROJECT)
    assert data['state'] == 'staged_requires_both_aspect_render_and_review'
    assert data['originalPublicSourcesModified'] is False
    assert len(data['files']) == 11
    for row in data['files']:
        target = PROJECT / row['path']
        assert target.resolve().is_relative_to(PROJECT.resolve()), 'Staged path escaped project'
        assert sha(target) == row['afterSha256'], 'Staged input changed: ' + row['path']
    for row in data['voiceProofs']:
        assert sha(Path(row['path'])) == row['sha256'], 'Voice proof changed'
    return {'manifestSha256': MANIFEST_SHA, 'rendererSha256': RENDERER_SHA,
            'stagedFilesVerified': 11, 'voiceProofsVerified': len(data['voiceProofs'])}


def main():
    proof = preflight()
    if '--check' in sys.argv:
        print(json.dumps({'state': 'preflight_verified', **proof}))
        return
    STATUS.parent.mkdir(exist_ok=True)
    with STATUS.with_suffix('.lock').open('a+') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if STATUS.exists():
            previous = json.loads(STATUS.read_text())
            if previous.get('state') in ['halted', 'complete_rendered_needs_review']:
                raise ValueError('Explicit reconciliation required before restarting a finished correction job')
        state = {'state': 'starting', 'pid': os.getpid(), 'startedAt': now(), **proof,
                 'project': str(PROJECT), 'qualityApproved': False, 'paidCalls': 0,
                 'jobs': 1, 'workers': 4, 'nice': 10}
        save(state)
        try:
            child = subprocess.Popen([NODE, '--import', 'tsx', 'scripts/render-teaching-corrections.ts',
                                      '--project', str(PROJECT), '--execute'], cwd=APP)
            state.update(state='rendering', renderControllerPid=child.pid)
            save(state)
            code = child.wait()
            state.update(state='complete_rendered_needs_review' if code == 0 else 'halted',
                         exitCode=code, finishedAt=now(), automaticRetry=False)
            save(state)
            if code:
                raise SystemExit(code)
        except BaseException as error:
            state.update(state='halted', errorType=type(error).__name__, error=str(error)[:500],
                         finishedAt=now(), automaticRetry=False)
            save(state)
            raise


if __name__ == '__main__':
    main()
