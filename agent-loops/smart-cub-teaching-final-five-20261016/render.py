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
import time

RUN = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
APP = RUN / 'revision/smart-cub/apps/prototypes/founder-videos'
RUNNER = RUN / 'teaching-repairs/runners/final-five-fcb1d28acec8'
PROJECT = RUN / 'teaching-repairs/final-style-lab'
MANIFEST = RUN / 'teaching-repairs/final-source-patches.json'
MANIFEST_SHA = '9f26b980445f45cc5fff04fb0e673954dd73aa8a7005f5199031d38123657d88'
RENDERER_SHA = '19c22c5fcc8b9d7bda93c180b17f8bd7a719830543888e9dbc0c09deccc5c6f3'
HELPERS = {'scripts/render-teaching-corrections.ts': RENDERER_SHA,
           'scripts/teaching-correction-selection.ts': '43113fa3e95ab09349daa653b3c270decd7e4d0ff9aa9f8296edaff4fb49d229',
           'scripts/render-batch-support.ts': '4d42133281a2d224db5c37e861ba69f6fc995fc011259407467486e3d68cf72e'}
STATUS = RUN / 'workflow/teaching-final-five-dispatch-status.json'
NODE = '/opt/homebrew/opt/node@22/bin/node'
PREDECESSOR_IDS = ['v3-y6-select-pronoun-form-wide', 'v3-y6-select-pronoun-form-tall',
                   'v3-y6-expand-common-abbreviation-wide', 'v3-y6-expand-common-abbreviation-tall']
PREDECESSOR_GRAPH = '7a362e17ab32876fbced3b1e17ec92f091c769f890fd35228a30da69e170b190'


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
        assert sha(RUNNER / relative) == expected, 'Render helper changed: ' + relative
    assert sha(RUNNER / 'runner-manifest.json') == '7ff524d722da36400f782965fbcfdbdee79f24612ddfb217c3f681a5f8bce0e1', 'Runner manifest changed'
    assert json.loads((RUNNER / 'package.json').read_text()) == {'type': 'module'}, 'Runner module mode changed'
    assert (RUNNER / 'node_modules').is_symlink() and (RUNNER / 'node_modules').resolve() == (APP / 'node_modules').resolve(), 'Runner dependency target changed'
    data = json.loads(MANIFEST.read_text())
    assert data['project'] == str(PROJECT)
    assert data['selection'] == 'final-five'
    assert data['state'] == 'staged_requires_both_aspect_render_and_review'
    assert data['originalPublicSourcesModified'] is False
    assert len(data['files']) == 17
    for row in data['files']:
        target = PROJECT / row['path']
        assert target.resolve().is_relative_to(PROJECT.resolve()), 'Staged path escaped project'
        assert sha(target) == row['afterSha256'], 'Staged input changed: ' + row['path']
    assert len(data['voiceProofs']) == 5, 'Exactly five saved voice proofs required'
    implementation = data['copyImplementation']
    assert implementation['sha256'] == '70167bec537569b907a7c28f305505991b7ea286f538e734d3c3c251f16d5468', 'Approved18a2 copy implementation changed'
    assert Path(implementation['path']).resolve().is_relative_to(RUN.resolve())
    assert sha(Path(implementation['path'])) == implementation['sha256'], 'Source copy implementation changed'
    for row in data['voiceProofs']:
        assert sha(Path(row['path'])) == row['sha256'], 'Voice proof changed'
    temporary = Path(subprocess.check_output([NODE, '-p', 'require("node:os").tmpdir()'], text=True).strip())
    host_lease = temporary / 'smart-cub-founder-render.lock'
    assert not host_lease.exists(), 'Shared render lease requires ownership reconciliation: ' + str(host_lease)
    subprocess.run([str(RUN / '.venv/bin/python'), '-B', str(RUN / 'workflow/render_tmpdir.py')], check=True)
    return {'manifestSha256': MANIFEST_SHA, 'rendererSha256': RENDERER_SHA,
            'stagedFilesVerified': 17, 'voiceProofsVerified': len(data['voiceProofs']),
            'selection': 'final-five', 'helperHashes': HELPERS, 'sharedLeasePath': str(host_lease)}


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def predecessor_receipts(project):
    result = []
    for identity in PREDECESSOR_IDS:
        receipt_path = project / 'out' / (identity + '.source-receipt.json')
        receipt = json.loads(receipt_path.read_text())
        assert receipt['sourceSnapshot'] == PREDECESSOR_GRAPH
        assert receipt['selection'] == 'follow-up-pair'
        assert receipt['selectedCompositionIds'] == PREDECESSOR_IDS
        output = receipt['output']
        assert output['path'] == 'out/' + identity + '.mp4'
        media = project / output['path']
        assert media.stat().st_size == output['bytes'] and sha(media) == output['sha256']
        result.append({'id': identity, 'media': str(media), 'sha256': output['sha256'],
                       'bytes': output['bytes'], 'receipt': str(receipt_path),
                       'receiptSha256': sha(receipt_path)})
    return result


def wait_for_predecessor(state):
    deadline = time.monotonic() + 7200
    status = RUN / 'workflow/teaching-follow-up-dispatch-status.json'
    while True:
        previous = json.loads(status.read_text())
        if previous['state'] == 'halted':
            raise ValueError('Follow-up renderer halted; explicit reconciliation required')
        if previous['state'] == 'complete_rendered_needs_review':
            assert previous['exitCode'] == 0 and previous['manifestSha256'] == '660f7f438ce6f0222f996c95a4f9a324c953d0aeb0cf76dd6fbc9722f2ac32bc'
            if not any(process_alive(previous[key]) for key in ('pid', 'renderControllerPid')):
                project = RUN / 'teaching-repairs/pronoun-style-lab'
                outputs = predecessor_receipts(project)
                canonical = Path(subprocess.check_output(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], text=True).strip())
                lease = canonical / 'smart-cub-founder-render.lock'
                cleanup = None
                if lease.exists():
                    owner_path = lease / 'owner.json'
                    raw = owner_path.read_bytes()
                    owner = json.loads(raw)
                    assert owner['pid'] == previous['renderControllerPid'] and not process_alive(owner['pid'])
                    assert owner['host'] == socket.gethostname() and owner['cwd'] == str(APP)
                    assert [path.name for path in lease.iterdir()] == ['owner.json']
                    archive = RUN / 'workflow/teaching-final-five-predecessor-lease-owner.json'
                    with archive.open('xb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    assert owner_path.read_bytes() == raw and not process_alive(owner['pid'])
                    owner_path.unlink()
                    lease.rmdir()
                    cleanup = {'ownerArchive': str(archive), 'sha256': sha(archive),
                               'removedOnlyProvenDeadPredecessorLease': True}
                proof = {'at': now(), 'sourceSnapshot': PREDECESSOR_GRAPH, 'outputs': outputs,
                         'predecessorParentsExited': True, 'deadLeaseCleanup': cleanup,
                         'canonicalLeasePath': str(lease), 'publicationApproval': False}
                target = RUN / 'workflow/teaching-final-five-predecessor-proof.json'
                if target.exists():
                    saved = json.loads(target.read_text())
                    assert saved['sourceSnapshot'] == PREDECESSOR_GRAPH and saved['outputs'] == outputs
                    assert saved['predecessorParentsExited'] is True
                    if saved.get('deadLeaseCleanup'):
                        archived = saved['deadLeaseCleanup']
                        assert sha(Path(archived['ownerArchive'])) == archived['sha256']
                    return {'predecessorProof': str(target), 'predecessorProofSha256': sha(target)}
                with target.open('x') as stream:
                    json.dump(proof, stream, indent=2)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                return {'predecessorProof': str(target), 'predecessorProofSha256': sha(target)}
        if time.monotonic() > deadline:
            raise TimeoutError('Follow-up predecessor did not finish within two hours')
        state.update(state='waiting-for-follow-up', predecessorState=previous['state'], checkedAt=now())
        save(state)
        time.sleep(20)


def main():
    assert re.sub(r'[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) == 'jamessmacstudio'
    if '--check' in sys.argv:
        proof = preflight()
        print(json.dumps({'state': 'preflight_verified', **proof}))
        return
    STATUS.parent.mkdir(exist_ok=True)
    with STATUS.with_suffix('.lock').open('a+') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if STATUS.exists():
            previous = json.loads(STATUS.read_text())
            if previous.get('state') in ['halted', 'complete_rendered_needs_review']:
                raise ValueError('Explicit reconciliation required before restarting a finished correction job')
        state = {'state': 'waiting-for-follow-up', 'pid': os.getpid(), 'startedAt': now(),
                 'project': str(PROJECT), 'qualityApproved': False, 'paidCalls': 0,
                 'jobs': 1, 'workers': 4, 'nice': 10}
        save(state)
        try:
            predecessor = wait_for_predecessor(state)
            proof = preflight()
            state.update(state='starting', **proof, **predecessor)
            save(state)
            child = subprocess.Popen([NODE, '--import', 'tsx', str(RUNNER / 'scripts/render-teaching-corrections.ts'),
                                      '--project', str(PROJECT), '--selection', 'final-five', '--execute'], cwd=APP)
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
