#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || exit 2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="${1:?Runtime repository required}"
python3 - "$SCRIPT_DIR" "$RUNTIME_ROOT" <<'PY'
import datetime,json,os,pathlib,plistlib,subprocess,sys
scripts,base=sys.argv[1:]
root=pathlib.Path(base)/'state/tutor-incremental-20261016-recovery-10h/agent-reviews'
job=json.loads((root/'job.json').read_text())
assert job['intervalSeconds']==1800
final=datetime.datetime.fromtimestamp(job['deadlineUnix']+60).astimezone()
label=job['launchdLabel']
assert label=='com.jud.tutor-agent-reviews-20261016'
value={'Label':label,'WorkingDirectory':base,'RunAtLoad':True,'ProcessType':'Standard',
 'ProgramArguments':[sys.executable,scripts+'/agent_review_dispatch.py','--run-dir',str(root)],
 'StartCalendarInterval':[{'Minute':0},{'Minute':30},{'Hour':final.hour,'Minute':final.minute}],
 'StandardOutPath':str(root/'stdout.log'),'StandardErrorPath':str(root/'stderr.log'),
 'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
path=pathlib.Path.home()/'Library/LaunchAgents'/(label+'.plist')
with path.open('wb') as stream:plistlib.dump(value,stream)
path.chmod(0o600)
subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True,timeout=10)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True,timeout=10)
print('Installed same-conversation agent review prompts at minutes 00 and 30; final review after the original deadline.')
PY
