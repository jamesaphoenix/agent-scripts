#!/usr/bin/env python3
"""Replace only the managed Docker cleanup cron with fail-closed image retention."""
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

MARKER = '# host-docker-cleanup (managed by agent-scripts, do not hand-edit this line)'
ROOT = Path('/opt/host-docker-cleanup')


def crontab_text(existing):
    # Refuse duplicate managed lines so a manual divergence is reviewed.
    if sum(MARKER in line for line in existing.splitlines()) > 1:
        raise ValueError('Multiple managed cleanup entries')
    kept = [line for line in existing.splitlines() if MARKER not in line]
    line = ('30 3 * * * /usr/bin/python3 /opt/host-docker-cleanup/cleanup-hetzner-images.py --cron '
            '>>/opt/host-docker-cleanup/state/logs/hetzner-images.log 2>&1 ' + MARKER)
    return '\n'.join(kept + [line]) + '\n'


def main():
    if os.geteuid() != 0 or Path(__file__).resolve().parent != ROOT:
        raise SystemExit('Install this source under /opt/host-docker-cleanup and run as root')
    state = ROOT/'state'; (state/'logs').mkdir(parents=True, exist_ok=True)
    if (state/'disabled').exists():
        raise SystemExit('Cleanup is disabled; resolve the operator pause before installation')
    subprocess.run(['/usr/bin/python3',str(ROOT/'cleanup-hetzner-images.py'),
                    '--plan-out',str(state/'installation-plan.json')], check=True)
    result = subprocess.run(['crontab','-l'], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit('Cannot read existing root crontab; refusing to replace it')
    backup = state/('crontab-before-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.txt')
    with backup.open('x') as stream:
        os.chmod(backup, 0o600); stream.write(result.stdout)
    subprocess.run(['crontab','-'], input=crontab_text(result.stdout), text=True, check=True)
    print('Installed image-only cleanup at 03:30; preserved other cron entries.')


if __name__ == '__main__': main()
