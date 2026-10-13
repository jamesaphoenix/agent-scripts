#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -un)" != "jamesphoenix" ]]; then
  echo 'CI disk admission is Mac Studio only.' >&2
  exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$SCRIPT_DIR" <<'PY'
from pathlib import Path
import shutil
import sys
import time
hook=Path(sys.argv[1])/'job-started.sh'
for name in ('actions-runner','actions-runner-2','actions-runner-3','actions-runner-4'):
    root=Path.home()/name;env=root/'.env'
    if not env.exists():raise SystemExit('Missing runner configuration: '+name)
    for line in env.read_text().splitlines():
        if line.startswith('ACTIONS_RUNNER_HOOK_JOB_STARTED=') and line.split('=',1)[1]!=str(hook):
            raise SystemExit('Existing different start hook retained: '+name)
    backup=root/('.env.pre-disk-budget-'+str(time.time_ns()))
    shutil.copy2(env,backup);backup.chmod(0o600)
    lines=[line for line in env.read_text().splitlines() if not line.startswith('ACTIONS_RUNNER_HOOK_JOB_STARTED=')]
    lines.append('ACTIONS_RUNNER_HOOK_JOB_STARTED='+str(hook))
    env.write_text('\n'.join(lines)+'\n');env.chmod(0o600)
    print('Configured disk admission:',name,'(takes effect after idle runner restart)')
PY
