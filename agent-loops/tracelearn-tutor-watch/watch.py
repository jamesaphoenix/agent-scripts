#!/usr/bin/env python3
"""Finite, secret-free health checks for the existing tutor discovery checkpoint."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

UTC = datetime.timezone.utc
CRAWLER_LABEL = "com.jud.tracelearn-tutor-countywide-20261010"
WATCH_LABEL = "com.jud.tracelearn-tutor-watch-20261010"
STALE_SECONDS = 15 * 60


def parse_time(value):
    result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must have a timezone")
    return result.astimezone(UTC)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def initialize(root, now):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "monitor.json"
    if path.exists():
        value = json.loads(path.read_text())
        if parse_time(value["deadline"]) - parse_time(value["startedAt"]) != datetime.timedelta(hours=8):
            raise ValueError("Original monitor window is invalid")
        return value
    value = {"startedAt": now.isoformat(), "deadline": (now + datetime.timedelta(hours=8)).isoformat(),
             "intervalSeconds": 1800, "maxRecoveries": 3}
    atomic_json(path, value)
    return value


def progress(snapshot):
    return {"pages": snapshot["pages"], "sources": snapshot["sources"], "sites": snapshot["sites"],
            "settled": snapshot["queries"].get("succeeded", 0) + snapshot["queries"].get("failed", 0),
            "spentCents": snapshot["budget"]["spentCents"]}


def paid_busy(snapshot):
    return (snapshot.get("checkpointPaidBusy", False) or snapshot["budget"]["reservedCents"] > 0 or snapshot["budget"]["uncertainCents"] > 0
            or any(snapshot["queries"].get(key, 0) for key in ["reserved", "submitting", "uncertain"]))


def assess(snapshot, job, previous, now):
    def result(health, reason, action="none"):
        return {"health": health, "reason": reason, "action": action}

    budget = snapshot["budget"]
    if (snapshot["runId"] != job["runId"] or parse_time(snapshot["deadlineAt"]) != parse_time(job["deadline"])
            or not 100 <= job["budgetCents"] <= 2000 or job["priorExposureCents"] + job["budgetCents"] > 10000
            or budget["exposureCents"] + budget["remainingCents"] != job["budgetCents"]
            or budget["exposureCents"] > job["budgetCents"] or min(budget.values()) < 0
            or not 0 <= snapshot["pages"] <= snapshot["maxPages"] <= 1000
            or not 0 <= snapshot["sites"] <= snapshot["maxSites"] <= 500):
        return result("invalid", "Immutable identity, deadline, budget or crawl limit mismatch")
    if paid_busy(snapshot):
        return result("held", "Paid request is active or uncertain; never interrupt or replay it")
    if now >= parse_time(snapshot["deadlineAt"]):
        return result("deadline", "Original discovery deadline reached", "stop" if snapshot["processLive"] else "none")
    if snapshot["status"] == "complete":
        return result("finished", "Crawler recorded frontier completion")
    intentional_stop = snapshot["stopReason"] not in ["", "process_interrupted"]
    if intentional_stop or snapshot["paidStopReason"]:
        return result("held", "Recorded limit or provider hold: " + (snapshot["stopReason"] or snapshot["paidStopReason"]))
    stale = (now - parse_time(snapshot["updatedAt"])).total_seconds() >= STALE_SECONDS
    changed = previous is None or previous["progress"] != progress(snapshot)
    no_progress = previous is not None and not changed and (now - parse_time(previous["lastProgressAt"])).total_seconds() >= STALE_SECONDS
    if snapshot["processLive"] and now < parse_time(snapshot["nextQueryAt"]):
        return result("cooldown", "Crawler is respecting its saved search pacing or provider cooldown")
    if not snapshot["processLive"] and not stale and not no_progress:
        return result("starting", "Allow credential resolution and launchd recovery to finish")
    if stale or no_progress or not snapshot["processLive"]:
        if previous and previous.get("recoveries", 0) >= 3:
            return result("held", "Three recovery attempts already requested; manual investigation required")
        return result("stalled", "No checkpoint progress for fifteen minutes or missing owner", "restart")
    return result("healthy", "Live owner and recent checkpoint progress")


def owned_processes(rows, binary, checkpoint):
    owners = []
    for line in rows.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            args = shlex.split(parts[1])
            state = args[args.index("--state") + 1] if "--state" in args else next((a[8:] for a in args if a.startswith("--state=")), "")
            if len(args) > 1 and args[0] == binary and args[1] == "discover" and state == checkpoint:
                owners.append(int(parts[0]))
        except (ValueError, IndexError):
            continue
    return owners


def command(args, timeout=20):
    # This watcher does not use credentials or inherit them into child processes.
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ["TOKEN", "PASSWORD", "SECRET", "API_KEY", "DATAFORSEO"])}
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, env=env)


def read_snapshot(run_dir, binary):
    job = json.loads((run_dir / "job.json").read_text())
    for name, field in [("queries.json", "planSha256"), ("web-seeds.txt", "seedSha256")]:
        if hashlib.sha256((run_dir / name).read_bytes()).hexdigest() != job[field]:
            raise ValueError("Saved input digest mismatch: " + name)
    result = command([binary, "discover-status", "--state", str(run_dir / "discovery.json")])
    if result.returncode:
        raise ValueError("Crawler rejected its checkpoint; exit " + str(result.returncode))
    snapshot = json.loads(result.stdout)
    checkpoint = json.loads((run_dir / "discovery.json").read_text())
    if checkpoint["config"]["budgetCents"] != job["budgetCents"]:
        raise ValueError("Saved budget changed")
    snapshot["checkpointPaidBusy"] = any(intent["status"] in ["reserved", "submitting", "uncertain"] for intent in checkpoint["intents"])
    return job, snapshot


def recover(run_dir, binary, previous, requested):
    job, fresh = read_snapshot(run_dir, binary)
    decision = assess(fresh, job, previous, datetime.datetime.now(UTC))
    if decision["action"] != requested or paid_busy(fresh):
        return "held_after_recheck"
    checkpoint = str(run_dir / "discovery.json")
    rows = command(["/bin/ps", "-axo", "pid=,command="]).stdout
    owners = owned_processes(rows, binary, checkpoint)
    if bool(fresh["processLive"]) != bool(owners) or len(owners) > 1:
        return "held_process_identity"
    domain = f"gui/{os.getuid()}/{CRAWLER_LABEL}"
    if command(["/bin/launchctl", "print", domain]).returncode:
        return "held_unregistered_runtime"
    if owners:
        # SIGTERM uses the Go signal handler and preserves immutable receipts.
        # Never force-kill. A concurrent paid request remains uncertain on resume.
        pid = owners[0]
        if owned_processes(command(["/bin/ps", "-p", str(pid), "-o", "pid=,command="]).stdout, binary, checkpoint) != [pid]:
            return "held_process_changed"
        os.kill(pid, signal.SIGTERM)
        until = time.monotonic() + 6
        while time.monotonic() < until:
            if not owned_processes(command(["/bin/ps", "-p", str(pid), "-o", "pid=,command="], 5).stdout, binary, checkpoint):
                break
            time.sleep(0.2)
        else:
            return "held_graceful_shutdown_timeout"
    if requested == "stop":
        return "deadline_stop_requested"
    # Do not kill an existing wrapper or install another owner. launchd and the
    # Go advisory lock retain sole ownership of the same original checkpoint.
    result = command(["/bin/launchctl", "kickstart", domain])
    return "restart_requested" if result.returncode == 0 else "held_launchctl_exit_" + str(result.returncode)


def check(run_dir, monitor_dir, binary):
    now = datetime.datetime.now(UTC)
    config = initialize(monitor_dir, now)
    previous_file = monitor_dir / "latest.json"
    previous = json.loads(previous_file.read_text()) if previous_file.exists() else None
    expired = now >= parse_time(config["deadline"])
    report = {"checkedAt": now.isoformat(), "monitorDeadline": config["deadline"], "monitorExpired": expired,
              "recoveries": previous.get("recoveries", 0) if previous else 0}
    try:
        job, snapshot = read_snapshot(run_dir, binary)
        report.update(snapshot=snapshot, progress=progress(snapshot))
        changed = previous is None or previous.get("progress") != report["progress"]
        report["lastProgressAt"] = now.isoformat() if changed else previous["lastProgressAt"]
        decision = assess(snapshot, job, previous if previous and "progress" in previous else None, now)
        report.update(decision)
        if not expired and decision["action"] != "none":
            if decision["action"] == "restart":
                report["recoveries"] += 1
            report["recoveryResult"] = recover(run_dir, binary, previous, decision["action"])
        queries = snapshot["queries"]
        if queries.get("failed", 0) >= 3 and queries.get("succeeded", 0) == 0:
            report["warning"] = "Search engines have not produced a successful receipt; review provider failures"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report.update(health="invalid", action="none", reason=type(error).__name__ + ": " + str(error)[:300])
    reports = monitor_dir / "reports"
    reports.mkdir(exist_ok=True, mode=0o700)
    atomic_json(reports / (now.strftime("%Y%m%dT%H%M%SZ") + ".json"), report)
    atomic_json(previous_file, report)
    print(json.dumps(report))
    if expired:
        # Save the final report before unloading this finite monitor. The
        # independently scheduled crawler retains its own original deadline.
        command(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{WATCH_LABEL}"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--monitor-dir", required=True, type=Path)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()
    args.monitor_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.monitor_dir / "check.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if args.init:
            initialize(args.monitor_dir, datetime.datetime.now(UTC))
        else:
            check(args.run_dir, args.monitor_dir, args.binary)


if __name__ == "__main__":
    main()
