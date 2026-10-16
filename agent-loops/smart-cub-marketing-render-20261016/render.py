#!/usr/bin/env python3
"""Resume the authorised exact-source marketing batch; no paid calls or automatic approval."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess

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

def main():
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
        save(state)
        try:
            code="import {SourceSnapshotter} from './scripts/render-batch-support.ts';console.log(new SourceSnapshotter(process.cwd()).snapshot().fingerprint)"
            actual=subprocess.check_output([NODE,'--import','tsx','--input-type=module','-e',code],cwd=APP,text=True).strip()
            if actual!=FROZEN:raise ValueError('Source freeze changed; render handoff refused')
            state.update({'phase':'rendering','sourceSnapshot':actual});save(state)
            child=subprocess.Popen([NODE,'--import','tsx','scripts/render-batch.ts','--ids','marketing',
                                    '--jobs','2','--workers','4','--execute'],cwd=APP)
            state.update({'renderControllerPid':child.pid});save(state)
            result=child.wait()
            state.update({'phase':'completed' if result==0 else 'halted','exitCode':result,
                          'finishedAt':now(),'publicationApproval':False,'automaticRetry':False});save(state)
            if result:raise SystemExit(result)
        except BaseException as error:
            state.update({'phase':'halted','errorType':type(error).__name__,'error':str(error)[:500],
                          'finishedAt':now(),'publicationApproval':False,'automaticRetry':False});save(state)
            raise

if __name__=='__main__':main()
