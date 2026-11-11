#!/usr/bin/env python3
"""Conservative daily cleanup for the dedicated A Series CI Docker daemon.

Every Docker call carries an explicit DOCKER_HOST (--docker-host or the environment;
there is no default). Daemon-wide prunes (volume, image, builder) run only when that
host is the CI daemon socket, so this job can never prune the production daemon that
also runs staging and production stacks. Ephemeral-container cleanup stays scoped by
the com.jud.ephemeral label and is safe on any daemon.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

# Rootless daemon of ci-runner (uid 1001). See rootless/setup-ci-rootless-docker.sh.
CI_DOCKER_SOCKET = 'unix:///run/user/1001/docker.sock'
PRUNE_COMMANDS = {('volume', 'prune'), ('image', 'prune'), ('builder', 'prune'),
                  ('system', 'prune'), ('container', 'prune'), ('network', 'prune'), ('buildx', 'prune')}


class RefusedPrune(RuntimeError):
    pass


def is_prune(args):
    return tuple(args[:2]) in PRUNE_COMMANDS


def docker_env(host):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'XDG_RUNTIME_DIR')}
    env['DOCKER_HOST'] = host
    return env


def docker(host, *args, apply=True, capture=False):
    """Run `docker <args>` against `host`. Prune commands need the CI socket."""
    if not host:
        raise ValueError('DOCKER_HOST must be explicit')
    if is_prune(args) and host != CI_DOCKER_SOCKET:
        raise RefusedPrune('refusing "docker %s" on %s: prune is only allowed on %s' % (' '.join(args[:2]), host, CI_DOCKER_SOCKET))
    if capture:
        return subprocess.run(['docker', *args], check=True, capture_output=True, text=True, timeout=900, env=docker_env(host)).stdout
    print(('run' if apply else 'dry-run') + ' [' + host + ']: docker ' + ' '.join(args), flush=True)
    if apply:
        subprocess.run(['docker', *args], check=True, timeout=900, env=docker_env(host))
    return ''


def run(*args, apply=False):
    print(('run' if apply else 'dry-run') + ': ' + ' '.join(args), flush=True)
    if apply:
        subprocess.run(args, check=True, timeout=900)


def active_workers():
    result = subprocess.run(['pgrep', '-f', 'Runner.Worker'], capture_output=True, text=True)
    return [int(pid) for pid in result.stdout.split() if pid.isdecimal()]


def orphaned_containers(host, now=None, host_name=None, pid_exists=None):
    """Label-scoped: only containers carrying com.jud.ephemeral are ever considered."""
    now = now or datetime.now(timezone.utc)
    host_name = host_name or subprocess.check_output(['hostname', '-s'], text=True).strip()
    pid_exists = pid_exists or (lambda pid: Path('/proc', pid).exists())
    ids = docker(host, 'ps', '-aq', '--filter', 'label=com.jud.ephemeral', capture=True).split()
    for container_id in ids:
        item = json.loads(docker(host, 'inspect', container_id, capture=True))[0]
        labels = item['Config'].get('Labels') or {}
        if 'com.jud.ephemeral' not in labels:
            continue
        if labels.get('com.jud.creator-host') != host_name:
            continue
        pid = labels.get('com.jud.creator-pid', '')
        if not pid.isdecimal():
            continue
        created = datetime.fromisoformat(item['Created'].replace('Z', '+00:00'))
        if (now - created).total_seconds() < 3 * 3600:
            continue
        if pid_exists(pid):
            continue
        yield container_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--docker-host', default=os.environ.get('DOCKER_HOST'),
                        help='explicit daemon to clean (default: $DOCKER_HOST; required)')
    args = parser.parse_args(argv)
    if not args.docker_host:
        parser.error('DOCKER_HOST is required: pass --docker-host or set DOCKER_HOST')
    host = args.docker_host
    state = Path('/home/ci-runner/.local/state/a-series-ci')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (state / 'disabled').exists():
        print('A Series CI cleanup disabled')
        return 0
    with (state / 'cleanup.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another cleanup is running')
            return 0
        for container_id in orphaned_containers(host):
            docker(host, 'rm', '-f', container_id, apply=args.apply)
        if active_workers():
            print('Active runner jobs: skip cache and workspace pruning')
            return 0
        if host != CI_DOCKER_SOCKET:
            print('Docker host %s is not the CI daemon socket: skipping volume, image and builder pruning' % host)
        else:
            docker(host, 'volume', 'prune', '-f', apply=args.apply)
            docker(host, 'image', 'prune', '-f', apply=args.apply)
            docker(host, 'builder', 'prune', '-f', '--filter', 'until=168h', apply=args.apply)
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
    return 0


if __name__ == '__main__':
    sys.exit(main())
