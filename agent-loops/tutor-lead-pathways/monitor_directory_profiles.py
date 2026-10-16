#!/usr/bin/env python3
"""Observe the profile stage without making provider calls or killing owners."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

from directory_profiles import now, save


def cleanup(root):
    """Clean finished writes while preserving reusable public-source evidence."""
    with (root / 'owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        removed = []
        for name in ('job.json', 'state.json', 'profiles.json', 'bucks-private-tutors.json',
                     'bucks-private-tutors.csv', 'status.json'):
            path = root / (name + '.tmp')
            if path.is_file() and not path.is_symlink():
                path.unlink()
                removed.append(str(path))
        report = {'completedAt': now(), 'removed': removed,
                  'retained': [str(root / name) for name in ('job.json', 'state.json', 'profiles.json',
                      'bucks-private-tutors.json', 'bucks-private-tutors.csv', 'pages', 'receipts',
                      'seed-batches', 'request-times.json', 'monitor')],
                  'note': 'Final exports, source receipts, reusable caches and the source checkout awaiting PR review are retained.'}
        save(root / 'cleanup.json', report)
        return report


def health(status, job, live, current):
    terminal = status.get("phase") == "stage_complete" or current >= job["deadlineUnix"]
    reached = status.get("bucksPrivateProfiles", 0) >= job["target"]
    observed = datetime.datetime.fromisoformat(status["observedAt"]).timestamp()
    state = "stage_complete" if terminal else "healthy" if live and current - observed <= 600 else "degraded"
    return {"health": state, "stageTerminal": terminal, "targetReached": reached,
            "bucksPrivateProfiles": status.get("bucksPrivateProfiles", 0), "attempts": status.get("attempts", 0),
            "queue": status.get("queue", 0), "checkpointAgeSeconds": max(0, current - observed), "processLive": live}


def watch(root, owner_label, monitor_label):
    for label in (owner_label, monitor_label):
        if not re.fullmatch(r"com\.jud\.[a-z0-9-]+", label):
            raise ValueError("Expected a registered launchd label")
    job = json.loads((root / "job.json").read_text())
    status = json.loads((root / "status.json").read_text())
    try:
        command = subprocess.run(["ps", "-p", str(status.get("pid", 0)), "-o", "command="], capture_output=True, text=True, timeout=10)
        live = command.returncode == 0 and "directory_profiles.py" in command.stdout and str(root) in command.stdout
    except (OSError, subprocess.TimeoutExpired):
        live = None
    report = dict(health(status, job, live, time.time()), checkedAt=now(), action="none", ownerPid=status.get("pid"), paidCostUsd=0)
    previous = root / "monitor/latest.json"
    recoveries = json.loads(previous.read_text()).get("recoveries", 0) if previous.exists() else 0
    if not report["stageTerminal"] and live is False and recoveries < 3:
        # Without -k, kickstart cannot terminate an owner that raced this observation.
        try:
            result = subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{owner_label}"], capture_output=True, timeout=10)
            report["action"] = "restart_requested" if result.returncode == 0 else "registered_restart_unavailable"
        except (OSError, subprocess.TimeoutExpired):
            report['action'] = 'restart_status_unconfirmed'
        recoveries += 1
    report["recoveries"] = recoveries
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    save(root / "monitor/reports" / (stamp + ".json"), report)
    save(previous, report)
    print(json.dumps(report))
    if report["stageTerminal"] and live is False and cleanup(root) is not None:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{monitor_label}"], capture_output=True, timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--owner-label", required=True)
    parser.add_argument("--monitor-label", required=True)
    args = parser.parse_args()
    watch(args.run_dir, args.owner_label, args.monitor_label)
