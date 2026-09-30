#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
if [[ "$(id -un)" != "jamesphoenix" ]]; then
  echo "Scheduled cache hygiene is Mac Studio only. Use maintenance.py manually on the MacBook." >&2
  exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
clang -O2 -Wall -Wextra -pthread "$SCRIPT_DIR/unlink-cache.c" -o "$SCRIPT_DIR/unlink-cache"
chmod 700 "$SCRIPT_DIR/unlink-cache"
python3 - "$SCRIPT_DIR" "$ROOT_DIR" <<'PY'
import os
from pathlib import Path
import plistlib
import subprocess
import sys
script=Path(sys.argv[1]);root=Path(sys.argv[2]);state=root/'state/cache-hygiene'
state.mkdir(parents=True,exist_ok=True,mode=0o700)
label='com.jud.cache-hygiene'
path=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
data={'Label':label,'ProgramArguments':['/usr/bin/env','python3',str(script/'maintenance.py'),'--apply','--state-dir',str(state),'--projects-root',str(Path.home()/'agent-runtime/just-understanding-data'),'--projects-root',str(Path.home()/'octospark-autofix'),'--projects-root',str(Path.home()/'trace-learn-autofix')],'StartInterval':3600,'ProcessType':'Background','LowPriorityIO':True,'Nice':10,'StandardOutPath':str(state/'launchd.out.log'),'StandardErrorPath':str(state/'launchd.err.log'),'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
with path.open('wb') as f:plistlib.dump(data,f)
path.chmod(0o600)
domain=f'gui/{os.getuid()}'
subprocess.run(['launchctl','bootout',f'{domain}/{label}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
subprocess.run(['launchctl','bootstrap',domain,str(path)],check=True)
subprocess.run(['launchctl','enable',f'{domain}/{label}'],check=True)
print('Installed hourly Studio cache-only hygiene:',label)
PY
