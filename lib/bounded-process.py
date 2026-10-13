#!/usr/bin/env python3
"""Bound a host check and all its descendants on macOS and Linux."""
import argparse
import os
import signal
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, required=True)
    parser.add_argument('--grace', type=float, default=5)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.timeout <= 0 or args.grace < 0:
        parser.error('A command and positive deadline are required')
    child = subprocess.Popen(command, start_new_session=True)

    def stop():
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=args.grace)
        except subprocess.TimeoutExpired:
            pass
        # Kill descendants even when the immediate shell exited on TERM.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()

    def interrupted(signum, _frame):
        stop()
        raise SystemExit(128 + signum)

    for signum in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP]:
        signal.signal(signum, interrupted)
    try:
        code = child.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        stop()
        return 124
    # A shell may finish while a wedged background child retains the pipes.
    stop()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
