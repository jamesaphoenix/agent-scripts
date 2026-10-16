#!/bin/bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || exit 2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="${1:?Runtime repository root required}"
python3 - "$SCRIPT_DIR" "$RUNTIME_ROOT" <<'PY'
import os,pathlib,plistlib,subprocess,sys
scripts,root=sys.argv[1:];stage=pathlib.Path(root)/'state/tutor-endpoint-probe-20261016'
assert (stage/'job.json').exists()
label='com.jud.tutor-endpoint-probe-20261016'
value={'Label':label,'WorkingDirectory':root,'RunAtLoad':True,'ProcessType':'Standard',
 'ProgramArguments':['/bin/zsh','-f',scripts+'/endpoint-probe.zsh',root,sys.executable,'--run-dir',str(stage)],
 'StandardOutPath':str(stage/'stdout.log'),'StandardErrorPath':str(stage/'stderr.log'),
 'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin','PYTHONUNBUFFERED':'1'}}
path=pathlib.Path.home()/'Library/LaunchAgents'/(label+'.plist')
if path.exists():raise SystemExit('Probe already installed; inspect existing run rather than reinstalling')
with path.open('wb') as stream:plistlib.dump(value,stream)
path.chmod(0o600)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True,timeout=10)
print('Installed '+label)
PY
