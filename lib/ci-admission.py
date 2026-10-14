#!/usr/bin/env python3
"""Two heavy CI job leases; known lightweight control steps can complete promptly."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time


def is_control_job(environment):
    repository = environment.get('GITHUB_REPOSITORY', '')
    prefix = repository + '/.github/workflows/'
    workflow_ref = environment.get('GITHUB_WORKFLOW_REF', '')
    if not repository or not workflow_ref.startswith(prefix):
        return False
    workflow = workflow_ref[len(prefix):].split('@', 1)[0]
    policies = {
        ('Just-Understanding-Data-Ltd/octospark', 'release-compose.yml'): {'guard_trusted_ref', 'build_images_amd64', 'release_gate'},
        ('Just-Understanding-Data-Ltd/octospark', 'integration-tests.yml'): {'guard_trusted_ref', 'build_images_amd64', 'release_gate'},
        ('Just-Understanding-Data-Ltd/trace-learn', 'deploy-compose.yml'): {'guard_ref', 'build_images_amd64', 'release_gate'},
        ('Just-Understanding-Data-Ltd/trace-learn', 'lockfile-check.yml'): {'lockfile-check'},
        ('Just-Understanding-Data-Ltd/smart-cub-learning', 'deploy-compose.yml'): {'guard_ref', 'build_images_amd64', 'release_gate'},
        ('Just-Understanding-Data-Ltd/smart-cub-learning', 'lockfile-check.yml'): {'lockfile-check'},
    }
    return environment.get('GITHUB_JOB') in policies.get((repository, workflow), set())


def identity(pid):
    p = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'comm='], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def owner():
    pid = os.getppid()
    for _ in range(12):
        value = identity(pid)
        if value and 'Runner.Worker' in value:
            return pid, value
        parent = subprocess.check_output(['ps', '-p', str(pid), '-o', 'ppid='], text=True).strip()
        if not parent or int(parent) <= 1:
            break
        pid = int(parent)
    raise SystemExit('CI admission requires an active Runner.Worker ancestor')


def acquire(directory, pid, owner_identity, timeout=1800):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and identity(pid) == owner_identity:
        for index in range(2):
            lease = (directory / ('slot-' + str(index) + '.lock')).open('a+')
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lease, index
            except BlockingIOError:
                lease.close()
        time.sleep(1)
    raise SystemExit('CI admission timed out or the owning job ended')


def main():
    pid, owner_identity = owner()
    if is_control_job(os.environ):
        print('CI host admission: known lightweight control job; heavy leases preserved', flush=True)
        return
    directory = Path.home() / '.local/state/jud-ci-admission'
    lease, index = acquire(directory, pid, owner_identity)
    child = os.fork()
    if child:
        lease.close()
        print('CI host admission: slot ' + str(index + 1) + '/2', flush=True)
        return
    os.setsid()
    # Detach inherited pipes so the Actions hook can finish. The host worker owns the lease.
    null = os.open(os.devnull, os.O_RDWR)
    for fd in [0, 1, 2]:
        os.dup2(null, fd)
    while identity(pid) == owner_identity:
        time.sleep(2)
    lease.close()
    os._exit(0)


if __name__ == '__main__':
    main()
