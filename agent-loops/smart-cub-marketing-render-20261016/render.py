#!/usr/bin/env python3
"""Resume the authorised exact-source marketing batch; no paid calls or automatic approval."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import sys
import subprocess
import time

RUN = Path.home() / 'render-tmp/smart-cub-refresh-20261016'
APP = RUN / 'smart-cub/apps/prototypes/founder-videos'
STATUS = RUN / 'workflow/marketing-render-dispatch-status.json'
FROZEN = '03c83d8aae51a158de6ad10990902d98fb40d67a2853fe95fc8a2d103a8caf15'
NODE = '/opt/homebrew/opt/node@22/bin/node'

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def save(value):
    temporary=STATUS.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,STATUS)

def configure_revision():
    global APP, STATUS, FROZEN
    if '--revision-config' not in sys.argv:
        return None
    from revision_config import load_revision
    config_path = Path(sys.argv[sys.argv.index('--revision-config') + 1])
    data, config_sha = load_revision(config_path, RUN)
    APP = Path(data['app'])
    STATUS = RUN / 'workflow/marketing-revision-render-dispatch-status.json'
    FROZEN = data['frozenSourceSnapshot']
    return config_path, data, config_sha


def sha_config(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_for_marketing_phase(state):
    hold = RUN / 'workflow/marketing-revision-render.HOLD'
    deadline = time.monotonic() + 7200
    while hold.exists():
        state.update(phase='waiting-for-marketing-phase-release', checkedAt=now(),
                     holdFile=str(hold))
        state.pop('renderControllerPid', None)
        state.pop('activeSelection', None)
        save(state)
        if time.monotonic() >= deadline:
            raise TimeoutError('Marketing phase hold exceeded two hours; explicit reconciliation required')
        time.sleep(2)
    state.pop('holdFile', None)
    state.update(phase='rendering', checkedAt=now())
    save(state)


def main():
    revision = configure_revision()
    if '--check' in sys.argv:
        if revision is None:
            raise ValueError('An explicit revision config is required for preflight')
        print(json.dumps({'state': 'revision_preflight_verified', 'app': str(APP),
                          'configSha256': revision[2], 'sourceSnapshot': FROZEN}))
        return
    if re.sub(r'[^a-z0-9]','',socket.gethostname().split('.')[0].lower())!='jamessmacstudio':
        raise ValueError('Marketing rendering is Studio-only')
    STATUS.parent.mkdir(parents=True,exist_ok=True)
    with STATUS.with_suffix('.lock').open('a+') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        existing=json.loads(STATUS.read_text()) if STATUS.exists() else None
        if existing and existing.get('phase') in ['completed','halted']:
            print('Completed/halted render requires explicit operator reconciliation before restart',flush=True)
            return
        state={'version':1,'phase':'preflight','startedAt':now(),'pid':os.getpid(),
               'expectedSourceSnapshot':FROZEN,'publicationApproval':False,'jobs':2,'workersPerRender':4,'nice':10}
        if revision:state['revisionConfigSha256']=revision[2]
        save(state)
        try:
            code="import {SourceSnapshotter} from './scripts/render-batch-support.ts';console.log(new SourceSnapshotter(process.cwd()).snapshot().fingerprint)"
            actual=subprocess.check_output([NODE,'--import','tsx','--input-type=module','-e',code],cwd=APP,text=True).strip()
            if actual!=FROZEN:raise ValueError('Source freeze changed; render handoff refused')
            if revision and sha_config(revision[0]) != revision[2]:raise ValueError('Revision config changed after preflight')
            state.update({'phase':'rendering','sourceSnapshot':actual});save(state)
            selections = ['ctas', 'marketing'] if revision else ['marketing']
            state['completedSelections'] = []
            for selection in selections:
                if revision and selection == 'marketing':
                    wait_for_marketing_phase(state)
                if revision and sha_config(revision[0]) != revision[2]:
                    raise ValueError('Revision config changed between render phases')
                child=subprocess.Popen([NODE,'--import','tsx','scripts/render-batch.ts','--ids',selection,
                                        '--jobs','2','--workers','4','--execute'],cwd=APP)
                state.update({'renderControllerPid':child.pid,'activeSelection':selection});save(state)
                result=child.wait()
                if result:raise RuntimeError(f'Render phase {selection} exited {result}; no automatic retry')
                state['completedSelections'].append(selection);save(state)
            state.update({'phase':'completed','exitCode':0,'finishedAt':now(),
                          'publicationApproval':False,'automaticRetry':False})
            state.pop('activeSelection',None);state.pop('renderControllerPid',None);save(state)
        except BaseException as error:
            state.update({'phase':'halted','errorType':type(error).__name__,'error':str(error)[:500],
                          'finishedAt':now(),'publicationApproval':False,'automaticRetry':False});save(state)
            raise

if __name__=='__main__':main()
