#!/usr/bin/env python3
"""Install through scripts/install-launchd-tasks.sh on Mac Studio only."""
import os
from pathlib import Path
import platform
import plistlib
import pwd
import shutil
import subprocess

if platform.system() != 'Darwin' or pwd.getpwuid(os.geteuid()).pw_name != 'jamesphoenix':
    raise SystemExit('Install this registered job on Mac Studio only')
os.umask(0o077)
state = Path.home() / '.local/state/synology-retention'
state.mkdir(parents=True, exist_ok=True)
label = 'com.jud.synology-retention'
path = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
path.write_bytes(plistlib.dumps({
    'Label': label, 'ProgramArguments': [shutil.which('python3'), str(Path(__file__).with_name('run.py').resolve())],
    'WorkingDirectory': str(state), 'RunAtLoad': True, 'StartInterval': 3600,
    'ProcessType': 'Background', 'Nice': 10, 'LowPriorityIO': True, 'ExitTimeOut': 450,
    'StandardOutPath': str(state / 'job.log'), 'StandardErrorPath': str(state / 'job.err.log'),
}))
domain = 'gui/' + str(os.getuid())
subprocess.run(['launchctl', 'bootout', domain + '/' + label], check=False, stderr=subprocess.DEVNULL)
subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
print('Installed hourly NAS retention audit. Deletion requires the NAS cleanup-enabled marker.')
