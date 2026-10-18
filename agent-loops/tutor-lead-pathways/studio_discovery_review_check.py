"""Studio-owned snapshots and reminders for the one authorised native agent."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from discovery import atomic_json, file_hash, now_iso

ROOT = Path('/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state')
CONTROL = ROOT / 'tutor-studio-20261018-free-10h'
AGENT = 'tutor-bucks-20261017'
EXPECTED_AGENT_CWD = '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/.worktrees/tutor-studio-free-20261017'
SOURCE_DIR = '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/.worktrees/tutor-discovery-10h-20261018'
LABEL = 'com.jud.tutor-studio-20261018-free-10h-review'


def check(now=None):
    auth_path = CONTROL / 'authorization.json'
    auth = json.loads(auth_path.read_text())
    from discovery_10h_window import validate, verify_files
    validate(auth)
    verify_files(auth_path)
    deadline = datetime.datetime.fromisoformat(auth['deadline'])
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if auth['deadline'] != '2026-10-18T22:06:54+00:00' or auth['monitorIntervalSeconds'] != 1800:
        raise ValueError('Studio review deadline or interval changed')
    slot = min(int(now.timestamp()) // 1800 * 1800, int(deadline.timestamp()))
    key = datetime.datetime.fromtimestamp(slot, datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = CONTROL / 'inspections' / (key + '.json')
    if now > deadline + datetime.timedelta(minutes=30):
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],capture_output=True,timeout=10)
        return
    if target.exists():
        return
    summary = {'observedAt': now_iso(), 'slot': key, 'deadline': auth['deadline'],
               'authorizationSha256': file_hash(auth_path), 'expectedAgentCwd': EXPECTED_AGENT_CWD,
               'sourceDir': SOURCE_DIR, 'actualAgentInspection': False}
    for name, path in [('qualification', ROOT/'tutor-qualification-20261018-free-10h/status.json'),
                       ('profiles', ROOT/'bucks-private-tutors-20261018-free-10h/status.json')]:
        if path.exists():
            data = json.loads(path.read_text())
            summary[name] = {k: data.get(k) for k in ('phase','observedAt','ownerPid','pid','counts','decisions',
                'attempts','queue','bucksPrivateProfiles','automatedQualification') if k in data}
    result = subprocess.run(['herdr','--session','default','agent','get',AGENT],
                            capture_output=True,text=True,timeout=15)
    if result.returncode == 0:
        agent = json.loads(result.stdout)['result']['agent']
        if agent.get('pane_id') != 'wA:p1' or agent.get('cwd') != EXPECTED_AGENT_CWD:
            raise ValueError('Studio agent target changed')
        summary['agentStatus'] = agent['agent_status']
    else:
        summary['agentStatus'] = 'unavailable'
    atomic_json(target, summary)
    if summary['agentStatus'] in ('idle','done') and now <= deadline + datetime.timedelta(minutes=30):
        prompt = ('Inspect the authorised Studio tutor continuation now, using HANDOFF.md and the control state. '
                  f'Write an actual inspection to {CONTROL}/reviews/{key}.json with reviewedAt, findings and actions. '
                  'Respect zero new allowance, unchanged filters and immutable 2026-10-18T22:06:54Z deadline. '
                  'At deadline verify final exports and perform the specified terminal cleanup. Do not spawn agents.')
        sent = subprocess.run(['herdr','--session','default','agent','prompt',AGENT,prompt],
                              capture_output=True,text=True,timeout=20)
        atomic_json(CONTROL/'deliveries'/(key+'.json'), {'at':now_iso(),'exitCode':sent.returncode,
                    'deliveryOnly':True,'actualAgentInspection':False})


if __name__ == '__main__':
    check()
