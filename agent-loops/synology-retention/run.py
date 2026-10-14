#!/usr/bin/env python3
"""One scheduled, bounded NAS audit/cleanup; no agent or secret-provider calls."""
import json
import os
from pathlib import Path
import subprocess
import time

state = Path.home() / '.local/state/synology-retention'
os.umask(0o077)
state.mkdir(parents=True, exist_ok=True)
if not (state / 'disabled').exists():
    program = Path(__file__).with_name('retention.py').read_text()
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
                             'mydocker@100.123.72.113', 'python3 - --apply'],
                            input=program, text=True, capture_output=True, timeout=420)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = {'checkedAt': time.time(), 'errors': ['NAS audit failed before producing a report'], 'exitCode': result.returncode}
    temporary = state / 'last.json.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(state / 'last.json')
    print(json.dumps({'checkedAt': report.get('checkedAt'), 'mode': report.get('mode'), 'skipped': report.get('skipped'),
                      'removedImages': len(report.get('removedImageIds', [])),
                      'removedReleases': len(report.get('removedReleasePaths', [])), 'errors': report.get('errors', [])}))
    raise SystemExit(result.returncode)
