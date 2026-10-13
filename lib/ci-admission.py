#!/usr/bin/env python3
"""Two host-wide CI job leases, owned by Runner.Worker rather than checkout paths."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time


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
