#!/usr/bin/env bash
# Invoke only through the registry-driven installer on Mac Studio.
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
mode="${1:?run or watch}"
manifest="${2:?immutable manifest path}"
[[ "$mode" == run || "$mode" == watch ]] || exit 2
[[ "$HOME" == /Users/jamesphoenix ]] || { echo 'Mac Studio only' >&2; exit 2; }
script_dir="$(cd "$(dirname "$0")" && pwd)"
/opt/homebrew/bin/python3 -B - "$manifest" <<'PY'
import datetime,json,sys,time
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text())
a=datetime.datetime.fromisoformat(m['authorised_at']).timestamp()
b=datetime.datetime.fromisoformat(m['hard_end']).timestamp()
if b-a != 21600 or time.time() >= b-240:
    raise SystemExit('Invalid or expired immutable six-hour budget')
PY
# System Python avoids any Homebrew expat linkage dependency during plist authoring.
/usr/bin/python3 - "$mode" "$manifest" "$script_dir" <<'PY'
import json,os,plistlib,subprocess,sys
from pathlib import Path
mode,manifest,root=sys.argv[1:]
m=json.loads(Path(manifest).read_text()); label=m['labels'][mode]
base=Path(m['base']); state=base/'state'; state.mkdir(parents=True,exist_ok=True)
plist=Path.home()/'Library/LaunchAgents'/(label+'.plist')
if plist.exists():
    raise SystemExit('Task plist already exists; inspect rather than reinstalling a live job')
config={'Label':label,'ProgramArguments':['/opt/homebrew/bin/python3','-B',root+'/overnight.py',mode,'--manifest',manifest],
        'WorkingDirectory':str(base),'RunAtLoad':True,'KeepAlive':False,'ProcessType':'Interactive',
        'StandardOutPath':str(state/(mode+'.log')),'StandardErrorPath':str(state/(mode+'.err.log')),
        'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
if mode=='watch': config['StartInterval']=60
plist.write_bytes(plistlib.dumps(config))
(state/(mode+'.installed.plist')).write_bytes(plist.read_bytes())
subprocess.run(['/usr/bin/plutil','-lint',str(plist)],check=True)
subprocess.run(['/bin/launchctl','bootstrap','gui/'+str(os.getuid()),str(plist)],check=True)
print('Registered',label,'manifest',manifest)
PY
