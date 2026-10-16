#!/usr/bin/python3
"""Bounded task-local CLI config; stdout only reaches the reviewer's secret pipe."""
import os
from pathlib import Path
import signal
import subprocess
import sys

config = Path.home() / 'render-tmp/smart-cub-refresh-20261016/workflow/marketing-review-op-config'
config.mkdir(parents=True, exist_ok=True, mode=0o700)
process = subprocess.Popen(['/opt/homebrew/bin/op', '--config', str(config), '--cache=false', *sys.argv[1:]],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
try:
    output, _ = process.communicate(timeout=25)
except subprocess.TimeoutExpired:
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
    sys.stderr.write('1Password CLI exceeded25seconds with task-local config; no retry\n')
    sys.exit(124)
if process.returncode:
    sys.stderr.write('1Password CLI failed; inspect service-account access without printing credentials\n')
    sys.exit(process.returncode)
os.write(1, output)
