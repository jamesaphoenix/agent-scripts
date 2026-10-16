#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || exit 2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:?Choose run or watch}"
STAGE="${2:?Prepared stage required}"
RUNTIME_ROOT="${3:?Runtime repository required}"
[[ "$MODE" == run || "$MODE" == watch ]] || exit 2
[[ "$STAGE" =~ ^tutor-incremental-[a-z0-9-]+$ ]] || exit 2
python3 - "$SCRIPT_DIR" "$RUNTIME_ROOT" "$STAGE" "$MODE" <<'PY'
import json,os,pathlib,plistlib,subprocess,sys
scripts,root,stage,mode=sys.argv[1:]
sys.path.insert(0,scripts)
from incremental_discovery import load_job,process_live
state=pathlib.Path(root)/'state'/stage
job,_=load_job(state)
owner='com.jud.'+stage
label=owner if mode=='run' else owner+'-watch'
if process_live(label):
 raise SystemExit('An installed owner is active; do not replace it')
value={'Label':label,'WorkingDirectory':root,'RunAtLoad':True,'ProcessType':'Standard',
 'ProgramArguments':['/bin/zsh','-f',scripts+'/incremental.zsh',root,sys.executable,'--run-dir',str(state)],
 'StandardOutPath':str(state/('stdout.log' if mode=='run' else 'watch-stdout.log')),
 'StandardErrorPath':str(state/('stderr.log' if mode=='run' else 'watch-stderr.log')),
 'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
if mode=='watch':
 value['ProgramArguments']=[sys.executable,scripts+'/incremental_discovery.py','watch','--run-dir',str(state)]
 value['StartInterval']=job['monitorIntervalSeconds']
path=pathlib.Path.home()/'Library/LaunchAgents'/(label+'.plist')
path.parent.mkdir(parents=True,exist_ok=True)
with path.open('wb') as stream:plistlib.dump(value,stream)
path.chmod(0o600)
subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True,timeout=10)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True,timeout=10)
print('Installed '+label)
PY
