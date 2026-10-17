#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || exit 2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${3:?Runtime repository required}"
MODE="${1:?Choose run or watch}"
STAGE="${2:?Choose a prepared stage}"
[[ "$MODE" == run || "$MODE" == watch ]] || exit 2
[[ "$STAGE" =~ ^tutor-qualification-[a-z0-9-]+$ ]] || exit 2
/opt/homebrew/opt/python@3.14/bin/python3.14 - "$SCRIPT_DIR" "$ROOT_DIR" "$STAGE" "$MODE" <<'PY'
import json,os,pathlib,plistlib,subprocess,sys
scripts,root,stage,mode=sys.argv[1:];state=pathlib.Path(root)/'state'/stage
job=json.loads((state/'job.json').read_text())
assert job['authorization']['additionalCapCents']==1000
assert job['authorization']['instruction']=='Allocate another $10-$20 for classification etc.'
owner='com.jud.'+stage;monitor=owner+'-watch';label=owner if mode=='run' else monitor
value={'Label':label,'ProgramArguments':['/bin/zsh','-f',scripts+'/qualify.zsh',root,sys.executable,'--run-dir',str(state)],
       'WorkingDirectory':root,'RunAtLoad':True,'ProcessType':'Standard',
       'StandardOutPath':str(state/('stdout.log' if mode=='run' else 'watch-stdout.log')),
       'StandardErrorPath':str(state/('stderr.log' if mode=='run' else 'watch-stderr.log')),
       'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
if mode=='watch':
 value['ProgramArguments']=[sys.executable,scripts+'/qualify_inventory.py','watch','--run-dir',str(state),
     '--owner-label',owner,'--monitor-label',monitor]
 value['StartInterval']=job.get('monitorIntervalSeconds',900)
path=pathlib.Path.home()/'Library/LaunchAgents'/f'{label}.plist'
path.parent.mkdir(parents=True,exist_ok=True)
with path.open('wb') as stream:plistlib.dump(value,stream)
path.chmod(0o600)
subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True)
print('Installed '+label)
PY
