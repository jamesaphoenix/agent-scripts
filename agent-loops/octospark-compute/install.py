#!/usr/bin/env python3
"""Install one registered native compute service on the authorised runtime host."""
import os
from pathlib import Path
import platform
import pwd
import plistlib
import shutil
import subprocess
import sys

role = sys.argv[1]
if role not in ['render', 'media-staging', 'media-prod', 'qwen']:
    raise SystemExit('Invalid role')
state = Path(os.environ.get('OCTOSPARK_COMPUTE_STATE', str(Path.home() / '.local/state/octospark-compute')))
(state / 'logs').mkdir(parents=True, exist_ok=True)
runner = Path(__file__).with_name('run.py').resolve()
python = shutil.which('python3')
label = 'com.jud.octospark-compute.' + role
if platform.system() == 'Darwin':
    if pwd.getpwuid(os.geteuid()).pw_name != 'jamesphoenix':
        raise SystemExit('Compute LaunchAgents are installed on Mac Studio only')
    dest = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    obj = dict(Label=label, ProgramArguments=[python, str(runner), role, str(state)], RunAtLoad=True, KeepAlive={'SuccessfulExit': False}, ThrottleInterval=30, ExitTimeOut=60, ProcessType='Background', WorkingDirectory=str(state), StandardOutPath=str(state / 'logs' / (role + '.out.log')), StandardErrorPath=str(state / 'logs' / (role + '.err.log')))
    dest.write_bytes(plistlib.dumps(obj))
    uid = str(os.getuid())
    subprocess.run(['launchctl', 'bootout', 'gui/' + uid + '/' + label], check=False, stderr=subprocess.DEVNULL)
    subprocess.run(['launchctl', 'bootstrap', 'gui/' + uid, str(dest)], check=True)
else:
    if os.geteuid() != 0:
        raise SystemExit('Systemd installation requires root')
    name = 'octospark-compute-' + role + '.service'
    dest = Path('/etc/systemd/system') / name
    dest.write_text('[Unit]\nDescription=Octospark native ' + role + '\nAfter=docker.service tailscaled.service network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nExecStart=' + python + ' ' + str(runner) + ' ' + role + ' ' + str(state) + '\nRestart=on-failure\nRestartSec=30\nTimeoutStopSec=60\nKillMode=mixed\n\n[Install]\nWantedBy=multi-user.target\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', name], check=True)
