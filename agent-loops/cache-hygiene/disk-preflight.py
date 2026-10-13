#!/usr/bin/env python3
"""Check available disk space before new large work. Never interrupt an active job."""
import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys

GIB=2**30

def budget_allows(free, floor, reservation, slots=1):
    if min(free,floor,reservation)<0 or slots<1:raise ValueError('Invalid disk budget')
    return free >= floor+reservation*slots

def worker_slots(commands):
    return max(1,sum(Path(c.strip()).name=='Runner.Worker' for c in commands))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minimum-free-gib',type=float,default=100)
    parser.add_argument('--expected-growth-gib',type=float,default=20)
    parser.add_argument('--ci',action='store_true',help='Reserve growth for every active CI worker')
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if args.minimum_free_gib<0 or args.expected_growth_gib<0:parser.error('Budgets must be nonnegative')
    try:
        free=int(subprocess.check_output(['df','-k','/System/Volumes/Data'],text=True,timeout=10).splitlines()[-1].split()[3])*1024
        slots=worker_slots(subprocess.check_output(['ps','-U',getpass.getuser(),'-o','comm='],text=True,timeout=10).splitlines()) if args.ci else 1
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('Disk budget unavailable; new work refused:',type(error).__name__,file=sys.stderr);return 75
    floor=int(args.minimum_free_gib*GIB);reserve=int(args.expected_growth_gib*GIB)
    allowed=budget_allows(free,floor,reserve,slots)
    print(f'Disk budget: {free/GIB:.1f} GiB free, {reserve*slots/GIB:.1f} GiB reserved for {slots} worker(s), {floor/GIB:.1f} GiB floor; '+('allowed' if allowed else 'new work refused'),flush=True)
    if not allowed:return 75
    command=args.command
    if command[:1]==['--']:command=command[1:]
    if command:os.execvp(command[0],command)
    return 0

if __name__=='__main__':sys.exit(main())
