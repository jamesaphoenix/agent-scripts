#!/usr/bin/env python3
"""Task-local review admission. This module never calls a provider or changes budget."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


FAMILY_SLOTS = {'teaching': (0, 1), 'marketing': (2,), 'corrected': (3,)}
POLICY = {'version': 1, 'maximumTotal': 4,
          'familyMaximums': {name: len(slots) for name, slots in FAMILY_SLOTS.items()}}


class AdmissionStopped(RuntimeError):
    pass


class Lease:
    def __init__(self, streams, stop_path):
        self.streams = streams
        self.stop_path = stop_path

    def popen(self, command, **kwargs):
        if self.stop_path and self.stop_path.exists():
            raise AdmissionStopped('Operator stop file present before child launch')
        if 'pass_fds' in kwargs or kwargs.get('close_fds') is False:
            raise ValueError('Admission exclusively owns inherited lease descriptors')
        # The child retains ownership if its dispatcher unexpectedly dies. Its own
        # subprocesses need not inherit these descriptors: review-media remains alive
        # throughout its provider request and durable reconciliation.
        return subprocess.Popen(command, pass_fds=tuple(s.fileno() for s in self.streams), **kwargs)


def try_lock(path):
    stream = path.open('a+')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    return stream


def write_metadata(stream, value):
    stream.seek(0)
    stream.truncate()
    json.dump(value, stream, sort_keys=True)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())


@contextlib.contextmanager
def admission(run_root, *, family, intent, stop_path=None, timeout=7200, poll_seconds=2):
    """Own one family slot and one exact media identity until the child exits.

    intent requires id, aspect and outputSha256. Families have disjoint reserved
    slots, so another family cannot consume the corrected-review reservation.
    Reusing an old unlocked inode is safe; lock files must never be unlinked.
    """
    if family not in FAMILY_SLOTS:
        raise ValueError('Unknown review family')
    if not isinstance(intent, dict) or set(intent) != {'id', 'aspect', 'outputSha256'}:
        raise ValueError('Exact id, aspect and outputSha256 intent required')
    if intent['aspect'] not in ('tall', 'wide') or not intent['id']:
        raise ValueError('Invalid exact review identity')
    digest = intent['outputSha256']
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Exact media SHA256 required')
    root = Path(run_root).resolve() / 'workflow/review-admission'
    root.mkdir(parents=True, exist_ok=True)
    stop_path = Path(stop_path) if stop_path else None
    # Family is deliberately excluded: two routes cannot review the same exact
    # identity concurrently, even if the caller labels one as a correction.
    identity = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    deadline = time.monotonic() + timeout
    owned = []
    try:
        while not owned:
            if stop_path and stop_path.exists():
                raise AdmissionStopped('Operator stop file present while waiting for review admission')
            with (root / 'admission.lock').open('a+') as controller:
                fcntl.flock(controller, fcntl.LOCK_EX)
                exact = try_lock(root / f'intent-{identity}.lock')
                if exact:
                    for slot in FAMILY_SLOTS[family]:
                        available = try_lock(root / f'slot-{slot}.lock')
                        if available:
                            owned = [exact, available]
                            metadata = {'version': 1, 'dispatcherPid': os.getpid(),
                                        'family': family, 'slot': slot, 'intent': intent,
                                        'admittedAtUnix': time.time(), 'publicationApproval': False}
                            for stream in owned:
                                write_metadata(stream, metadata)
                            break
                    if not owned:
                        exact.close()
            if not owned:
                if time.monotonic() >= deadline:
                    raise TimeoutError('No review admission before bounded timeout')
                time.sleep(poll_seconds)
        yield Lease(owned, stop_path)
    finally:
        for stream in reversed(owned):
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', required=True, type=Path)
    parser.add_argument('--family', required=True, choices=FAMILY_SLOTS)
    parser.add_argument('--id', required=True)
    parser.add_argument('--aspect', required=True, choices=['tall', 'wide'])
    parser.add_argument('--output-sha256', required=True)
    parser.add_argument('--stop-path', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('A single bounded review command is required')
    with admission(args.run_root, family=args.family,
                   intent={'id': args.id, 'aspect': args.aspect, 'outputSha256': args.output_sha256},
                   stop_path=args.stop_path) as lease:
        raise SystemExit(lease.popen(command).wait())


if __name__ == '__main__':
    main()
