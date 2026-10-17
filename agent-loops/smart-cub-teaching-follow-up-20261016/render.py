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
APP = RUN / 'revision/smart-cub/apps/prototypes/founder-videos'
PROJECT = RUN / 'teaching-repairs/pronoun-style-lab'
MANIFEST = RUN / 'teaching-repairs/follow-up-source-patches.json'
MANIFEST_SHA = '660f7f438ce6f0222f996c95a4f9a324c953d0aeb0cf76dd6fbc9722f2ac32bc'
RENDERER_SHA = '19c22c5fcc8b9d7bda93c180b17f8bd7a719830543888e9dbc0c09deccc5c6f3'
HELPERS = {'scripts/render-teaching-corrections.ts': RENDERER_SHA,
           'scripts/teaching-correction-selection.ts': 'c05be43bbc9d4a414f1af00f6a2b78ca0cbc56300fc644bb3651155eb0c4ac94',
           'scripts/render-batch-support.ts': '4d42133281a2d224db5c37e861ba69f6fc995fc011259407467486e3d68cf72e'}
STATUS = RUN / 'workflow/teaching-follow-up-dispatch-status.json'
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
    for relative, expected in HELPERS.items():
        assert sha(APP / relative) == expected, 'Render helper changed: ' + relative
    data = json.loads(MANIFEST.read_text())
    assert data['project'] == str(PROJECT)
    assert data['selection'] == 'follow-up-pair'
    assert data['state'] == 'staged_requires_both_aspect_render_and_review'
    assert data['originalPublicSourcesModified'] is False
    assert len(data['files']) == 6
    for row in data['files']:
        target = PROJECT / row['path']
        assert target.resolve().is_relative_to(PROJECT.resolve()), 'Staged path escaped project'
        assert sha(target) == row['afterSha256'], 'Staged input changed: ' + row['path']
    for row in data['voiceProofs']:
        assert sha(Path(row['path'])) == row['sha256'], 'Voice proof changed'
    temporary = Path(subprocess.check_output([NODE, '-p', 'require("node:os").tmpdir()'], text=True).strip())
    host_lease = temporary / 'smart-cub-founder-render.lock'
    assert not host_lease.exists(), 'Shared render lease requires ownership reconciliation: ' + str(host_lease)
    return {'manifestSha256': MANIFEST_SHA, 'rendererSha256': RENDERER_SHA,
            'stagedFilesVerified': 6, 'voiceProofsVerified': len(data['voiceProofs']),
            'selection': 'follow-up-pair', 'helperHashes': HELPERS, 'sharedLeasePath': str(host_lease)}


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
                                      '--project', str(PROJECT), '--selection', 'follow-up-pair', '--execute'], cwd=APP)
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
