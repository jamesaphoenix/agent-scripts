#!/usr/bin/env python3
"""Future Studio-only pause controller. Default prints commands, never pauses.

After James approves ONE window, --confirm-pause SESSION executes that window.
Run under remote nohup as documented in PLAN.md so SSH loss cannot kill cleanup.
"""
import argparse
import hashlib
import datetime
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from workload import SESSIONS

SCRIPT_DIR = Path(__file__).resolve().parent
LABEL = "com.jud.llama-server"
DOMAIN = f"gui/{os.getuid()}"
JOB = DOMAIN + "/" + LABEL
PLIST = Path.home() / "Library/LaunchAgents/com.jud.llama-server.plist"


def capture(command, check=True):
    return subprocess.run(command, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=12, check=check).stdout


def get(path, port=8082):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}" + path, timeout=3) as response:
        return json.load(response)


def resources():
    pressure = capture(["/usr/bin/memory_pressure"])
    match = re.search(r"System-wide memory free percentage: (\d+)%", pressure)
    if not match:
        raise RuntimeError("Cannot parse memory pressure")
    swap = capture(["/usr/sbin/sysctl", "vm.swapusage"])
    used = re.search(r"used = ([\d.]+)([KMG])", swap)
    if not used:
        raise RuntimeError("Cannot parse swap usage")
    sample = {"time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "pressure_free_pct": int(match[1]), "swap": swap.strip(),
              "disk_free_bytes": shutil.disk_usage(Path.home()).free}
    if sample["pressure_free_pct"] < 20 or float(used[1]) > 0 or sample["disk_free_bytes"] < 100_000_000_000:
        raise RuntimeError("Stop threshold: " + json.dumps(sample))
    return sample


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_group(process):
    if process is None:
        return
    # Worker and only its children share this newly created process group.
    for sig, seconds in ((signal.SIGTERM, 30), (signal.SIGKILL, 10)):
        if group_alive(process.pid):
            os.killpg(process.pid, sig)
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            process.poll()  # Reap the group leader if already exited.
            if not group_alive(process.pid):
                return
            time.sleep(0.5)
    if group_alive(process.pid):
        raise RuntimeError("Task processes still alive; refusing to overlap models")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", choices=SESSIONS)
    parser.add_argument("--confirm-pause", choices=SESSIONS)
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()
    if args.confirm_pause is None:
        subprocess.run([sys.executable, str(SCRIPT_DIR / "workload.py"), args.session,
                        "--out", str(args.out)], check=True)
        print("PRINT ONLY. No SSH, pause, model loading, or inference performed.")
        return
    if args.confirm_pause != args.session:
        raise RuntimeError("Confirmation must match the single session")
    if Path.home() != Path("/Users/jamesphoenix") or not socket.gethostname().lower().startswith("jamess"):
        raise RuntimeError("Execution is allowed only on James's Mac Studio")
    if get("/health").get("status") != "ok":
        raise RuntimeError("Live server not healthy before pause")
    if any(slot["is_processing"] for slot in get("/slots")):
        raise RuntimeError("Live server busy; do not pause")
    metrics = capture(["/usr/bin/curl", "-fsS", "--max-time", "3", "http://127.0.0.1:8082/metrics"])
    if not re.search(r"^llamacpp:requests_deferred 0$", metrics, re.M):
        raise RuntimeError("Queued requests or missing metrics; do not pause")
    listeners = capture(["/usr/sbin/lsof", "-nP", "-iTCP:8083", "-sTCP:LISTEN"], check=False)
    if listeners.strip():
        raise RuntimeError("Temporary port already occupied")
    data = PLIST.read_bytes()
    disabled = capture(["/usr/bin/plutil", "-extract", "EnvironmentVariables.LLAMA_SERVE_DISABLE_FILE",
                        "raw", "-o", "-", str(PLIST)]).strip()
    if Path(disabled).exists():
        raise RuntimeError("Existing disable marker; do not alter it")
    state = capture(["/bin/launchctl", "print", JOB])
    match = re.search(r"\bpid = (\d+)", state)
    if not match:
        raise RuntimeError("No original PID")
    original_pid = int(match[1])
    sample = resources()
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "original.plist").write_bytes(data)
    (args.out / "before.json").write_text(json.dumps({"original_pid": original_pid,
        "plist_sha256": hashlib.sha256(data).hexdigest(), "resources": sample}, indent=2))
    # Budget includes shutdown and workload; allow an additional 3 minutes for restore.
    workload_seconds = 840 if args.session in ("long2048", "long4096", "gpu") else 600
    if os.environ.get("LLAMA_WINDOW_SECONDS"):
        workload_seconds = max(60, min(900, int(os.environ["LLAMA_WINDOW_SECONDS"])))
    if os.environ.get("LLAMA_HARD_END"):
        hard_end = datetime.datetime.fromisoformat(os.environ["LLAMA_HARD_END"]).timestamp()
        workload_seconds = min(workload_seconds, int(hard_end - time.time() - 240))
        if workload_seconds < 60:
            raise RuntimeError("Insufficient time for workload plus recovery before immutable deadline")
    started = time.monotonic()
    pause_start_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print("Pausing ONE window:", args.session, "at", pause_start_utc,
          "workload cutoff seconds:", workload_seconds, flush=True)
    worker = None
    paused = False
    failure = None

    def interrupted(signum, frame):
        raise RuntimeError(f"Signal {signum}: restoring live server")

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        paused = True  # Restore even if bootout times out after having taken effect.
        capture(["/bin/launchctl", "bootout", JOB])
        until = time.monotonic() + 90
        while True:
            present = subprocess.run(["/bin/launchctl", "print", JOB], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=5).returncode == 0
            try:
                os.kill(original_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            if not present and not alive:
                break
            if time.monotonic() > until:
                raise RuntimeError("Original server did not unload in 90 seconds")
            time.sleep(1)
        env = os.environ.copy()
        env["LLAMA_BENCH_WINDOW_ACTIVE"] = "1"
        worker = subprocess.Popen([sys.executable, str(SCRIPT_DIR / "workload.py"), args.session,
                                   "--out", str(args.out / "workload"), "--execute"],
                                  env=env, start_new_session=True)
        (args.out / "worker.json").write_text(json.dumps({"pid": worker.pid, "pgid": worker.pid,
            "controller_pid": os.getpid(), "script_root": str(SCRIPT_DIR)}))
        with (args.out / "resources.jsonl").open("w") as log:
            while worker.poll() is None:
                if time.monotonic() - started > workload_seconds:
                    raise RuntimeError("Window workload deadline reached")
                log.write(json.dumps(resources()) + "\n")
                log.flush()
                time.sleep(5)
        if worker.returncode:
            raise RuntimeError(f"Workload failed with exit {worker.returncode}")
    except BaseException as error:
        failure = str(error)
        print("STOP:", failure, flush=True)
    finally:
        # Keep subsequent TERM/INT/HUP from interrupting restoration.
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        stop_group(worker)
        if paused:
            if PLIST.read_bytes() != data:
                print("ERROR: production plist changed during window; stop and report", flush=True)
                # Still restore the registered production file, never rewrite it.
                failure = failure or "Production plist changed externally"
            exists = subprocess.run(["/bin/launchctl", "print", JOB], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=5).returncode == 0
            if not exists:
                capture(["/bin/launchctl", "bootstrap", DOMAIN, str(PLIST)])
            until = time.monotonic() + 180
            while True:
                try:
                    healthy = get("/health").get("status") == "ok"
                    with urllib.request.urlopen("http://100.77.197.1:8080/v1/models", timeout=3) as response:
                        models = json.load(response)
                    alias = any(item.get("id") == "qwen3.8-flash-next" for item in models.get("data", []))
                    if healthy and alias:
                        break
                except Exception:
                    pass
                if time.monotonic() > until:
                    raise RuntimeError("RESTORE FAILED: health/alias not confirmed; no more tests")
                time.sleep(2)
            (args.out / "restored.json").write_text(json.dumps({"health": "ok", "models": models,
                "outage_seconds": time.monotonic() - started, "failure": failure,
                "pause_start_utc": pause_start_utc,
                "restored_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "workload_cutoff_seconds": workload_seconds,
                "plist_unchanged": PLIST.read_bytes() == data}, indent=2))
            print("Live server healthy; gateway alias confirmed. Outage seconds:",
                  round(time.monotonic() - started, 1), flush=True)
    if failure:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
