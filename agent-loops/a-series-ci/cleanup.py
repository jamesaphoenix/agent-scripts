#!/usr/bin/env python3
"""Conservative daily cleanup for the dedicated A Series CI Docker daemon."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import subprocess


def run(*args, apply=False):
    print(('run' if apply else 'dry-run') + ': ' + ' '.join(args), flush=True)
    if apply:
        subprocess.run(args, check=True, timeout=900)


def active_workers():
    result = subprocess.run(['pgrep', '-f', 'Runner.Worker'], capture_output=True, text=True)
    return [int(pid) for pid in result.stdout.split() if pid.isdecimal()]


def orphaned_containers():
    result = subprocess.run(['docker', 'ps', '-aq', '--filter', 'label=com.jud.ephemeral'], check=True, capture_output=True, text=True)
    for container_id in result.stdout.split():
        inspect = subprocess.run(['docker', 'inspect', container_id], check=True, capture_output=True, text=True)
        item = json.loads(inspect.stdout)[0]
        labels = item['Config'].get('Labels') or {}
        if labels.get('com.jud.creator-host') != subprocess.check_output(['hostname', '-s'], text=True).strip():
            continue
        pid = labels.get('com.jud.creator-pid', '')
        if not pid.isdecimal():
            continue
        created = datetime.fromisoformat(item['Created'].replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - created).total_seconds() < 3 * 3600:
            continue
        if Path('/proc', pid).exists():
            continue
        yield container_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    state = Path('/home/ci-runner/.local/state/a-series-ci')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (state / 'disabled').exists():
        print('A Series CI cleanup disabled')
        return
    with (state / 'cleanup.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another cleanup is running')
            return
        for container_id in orphaned_containers():
            run('docker', 'rm', '-f', container_id, apply=args.apply)
        if active_workers():
            print('Active runner jobs: skip cache and workspace pruning')
            return
        run('docker', 'volume', 'prune', '-f', apply=args.apply)
        run('docker', 'image', 'prune', '-f', apply=args.apply)
        run('docker', 'builder', 'prune', '-f', '--filter', 'until=168h', apply=args.apply)
        for runner in sorted(Path('/home/ci-runner').glob('actions-runner-[1-4]')):
            work = runner / '_work'
            if not work.is_dir():
                continue
            for directory in work.iterdir():
                if directory.name.startswith('_') or not directory.is_dir() or directory.is_symlink():
                    continue
                age = datetime.now(timezone.utc).timestamp() - directory.stat().st_mtime
                if age > 7 * 86400:
                    run('rm', '-rf', '--', str(directory), apply=args.apply)


if __name__ == '__main__':
    main()
