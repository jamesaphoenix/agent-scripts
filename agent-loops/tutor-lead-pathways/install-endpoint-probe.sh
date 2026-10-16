#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || exit 2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="${1:?Runtime repository root required}"
python3 - "$SCRIPT_DIR" "$RUNTIME_ROOT" <<'PY'
import json,os,pathlib,plistlib,subprocess,sys
scripts,root=sys.argv[1:];stage=pathlib.Path(root)/'state/tutor-endpoint-probe-20261016'
assert (stage/'job.json').exists()
label='com.jud.tutor-endpoint-probe-20261016'
value={'Label':label,'WorkingDirectory':root,'RunAtLoad':True,'StartInterval':1800,'ProcessType':'Standard',
 'ProgramArguments':['/bin/zsh','-f',scripts+'/endpoint-probe.zsh',root,sys.executable,'--run-dir',str(stage)],
 'StandardOutPath':str(stage/'stdout.log'),'StandardErrorPath':str(stage/'stderr.log'),
 'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
path=pathlib.Path.home()/'Library/LaunchAgents'/(label+'.plist')
if path.exists():
 old=plistlib.loads(path.read_bytes())
 if old['ProgramArguments']==value['ProgramArguments']:
  raise SystemExit('This exact probe source is already installed')
 live=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{label}'],capture_output=True,text=True,timeout=10)
 if any(x.strip().startswith('pid = ') for x in live.stdout.splitlines()):
  raise SystemExit('A probe owner is still active')
 status=json.loads((stage/'status.json').read_text())
 if status['phase']!='complete':raise SystemExit('Inspect the previous terminal probe state before replacing its source')
 backup=stage/'launchd-before-request-repair.plist'
 if not backup.exists():backup.write_bytes(path.read_bytes())
 subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True,timeout=10)
with path.open('wb') as stream:plistlib.dump(value,stream)
path.chmod(0o600)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True,timeout=10)
print('Installed '+label)
PY
