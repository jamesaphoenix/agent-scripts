#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="${AGENT_SCRIPTS_LAUNCHD_TASK_REGISTRY:-$ROOT/launchd-tasks/registry.json}"
MODE="install"
TASK_ID=""
ALL_HOSTS="0"

usage() {
  cat <<'HELP'
Usage:
  scripts/install-launchd-tasks.sh
  scripts/install-launchd-tasks.sh --dry-run
  scripts/install-launchd-tasks.sh --status
  scripts/install-launchd-tasks.sh --list
  scripts/install-launchd-tasks.sh --task TASK_ID [--dry-run|--status]
  scripts/install-launchd-tasks.sh --all-hosts --list

Installs registered launchd tasks for the current Mac user and host. The same
script runs on both machines and filters tasks by host and username.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install)
      MODE="install"
      shift
      ;;
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --status)
      MODE="status"
      shift
      ;;
    --list)
      MODE="list"
      shift
      ;;
    --task)
      TASK_ID="$2"
      shift 2
      ;;
    --all-hosts)
      ALL_HOSTS="1"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ] && [ -x /opt/homebrew/bin/python3 ]; then
  PYTHON_BIN="/opt/homebrew/bin/python3"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: python3 is required" >&2
  exit 1
fi

"$PYTHON_BIN" - "$ROOT" "$REGISTRY" "$MODE" "$TASK_ID" "$ALL_HOSTS" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(sys.argv[1])
REGISTRY = Path(sys.argv[2])
MODE = sys.argv[3]
TASK_ID = sys.argv[4]
ALL_HOSTS = sys.argv[5] == "1"


def run_text(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, check=False, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def host_candidates() -> set[str]:
    names = {
        run_text(["hostname"]),
        run_text(["hostname", "-s"]),
        run_text(["scutil", "--get", "LocalHostName"]),
        run_text(["scutil", "--get", "ComputerName"]),
    }
    cleaned = {name for name in names if name}
    with_local = {f"{name}.local" for name in cleaned if not name.endswith(".local")}
    return cleaned | with_local


def is_mac_studio(hosts: set[str]) -> bool:
    return bool(hosts & {"Jamess-Mac-Studio", "Jamess-Mac-Studio.local", "JamessMacStudio.fritz.box"})


def task_matches_current_host(task: dict[str, Any], user: str, hosts: set[str]) -> bool:
    if TASK_ID and task.get("id") != TASK_ID:
        return False
    if ALL_HOSTS:
        return True
    users = set(task.get("users") or [])
    task_hosts = set(task.get("hosts") or [])
    return (not users or user in users) and (not task_hosts or bool(task_hosts & hosts))


def selected_tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    user = run_text(["id", "-un"])
    hosts = host_candidates()
    tasks = [
        task
        for task in data.get("tasks", [])
        if task.get("enabled", True) and task_matches_current_host(task, user, hosts)
    ]
    if TASK_ID and not tasks:
        raise SystemExit(f"No enabled task matched {TASK_ID!r} for user {user} on hosts {sorted(hosts)}")
    return tasks


def display_task(task: dict[str, Any]) -> None:
    print(f"[{task['id']}] {task.get('name', task['id'])}")
    print(f"  label: {task.get('launchdLabel', '(none)')}")
    print(f"  schedule: {task.get('schedule', '(unspecified)')}")
    print(f"  hosts: {', '.join(task.get('hosts') or ['any'])}")
    print(f"  users: {', '.join(task.get('users') or ['any'])}")
    if task.get("requiresOpServiceAccount"):
        print("  requires: cached OP service account token")
    install = task.get("install") or {}
    if install.get("kind") == "command":
        print(f"  installer: {' '.join(install.get('command') or [])}")
        print(f"  cwd: {install.get('workdir')}")
    elif install.get("kind") == "plist":
        print(f"  plist: {install.get('plistPath')}")
    if task.get("notes"):
        print(f"  notes: {task['notes']}")


def ensure_op_service_account() -> None:
    script = ROOT / "scripts" / "cache-op-service-account-token.sh"
    if not script.exists():
        raise SystemExit(f"Missing OP service account cache script: {script}")
    subprocess.run([str(script), "--ensure"], check=True)


def install_plist(task: dict[str, Any], dry_run: bool) -> None:
    install = task["install"]
    source = ROOT / install["plistPath"]
    if not source.exists():
        raise SystemExit(f"Missing plist for task {task['id']}: {source}")
    dest_dir = Path.home() / "Library" / "LaunchAgents"
    dest = dest_dir / source.name
    label = task.get("launchdLabel")
    uid = os.getuid()
    print(f"  copy plist: {source} -> {dest}")
    print(f"  bootstrap: gui/{uid} {dest}")
    if dry_run:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    if label:
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], check=False, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)], check=True)
    if install.get("kickstart", False) and label:
        subprocess.run(["launchctl", "kickstart", f"gui/{uid}/{label}"], check=True)


def install_command(task: dict[str, Any], dry_run: bool) -> None:
    install = task["install"]
    command = install.get("command") or []
    workdir = Path(install.get("workdir") or ROOT)
    if not workdir.exists():
        raise SystemExit(f"Missing workdir for task {task['id']}: {workdir}")
    print(f"  run: {' '.join(command)}")
    print(f"  cwd: {workdir}")
    if dry_run:
        return
    subprocess.run(command, cwd=workdir, check=True)


def install_task(task: dict[str, Any], dry_run: bool) -> None:
    print(f"Installing {task['id']}: {task.get('name', task['id'])}")
    if task.get("requiresOpServiceAccount"):
        print("  ensuring cached OP service account token")
        if not dry_run:
            ensure_op_service_account()
    kind = (task.get("install") or {}).get("kind")
    if kind == "plist":
        install_plist(task, dry_run)
    elif kind == "command":
        install_command(task, dry_run)
    else:
        raise SystemExit(f"Unknown install kind for task {task['id']}: {kind}")


def status_task(task: dict[str, Any]) -> None:
    label = task.get("launchdLabel")
    print(f"[{task['id']}] {task.get('name', task['id'])}")
    if not label:
        print("  launchd: no label")
        return
    uid = os.getuid()
    list_result = subprocess.run(["launchctl", "list"], capture_output=True, check=False, text=True)
    loaded = label in list_result.stdout
    print(f"  launchd: {'loaded' if loaded else 'not loaded'} ({label})")
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    print(f"  plist: {'installed' if plist.exists() else 'missing'} ({plist})")
    print_result = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{label}"],
        capture_output=True,
        check=False,
        text=True,
    )
    if print_result.returncode != 0:
        return
    wanted = ("state =", "last exit code =", "runs =", "program =", "path =", "run interval =")
    for line in print_result.stdout.splitlines():
        stripped = line.strip()
        if any(item in stripped for item in wanted):
            print(f"  {stripped}")


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    hosts = host_candidates()
    if MODE in {"install", "dry-run"} and not is_mac_studio(hosts):
        print("Launchd task installation is Mac Studio-only; no action on this Mac.")
        print(f"Detected hosts: {', '.join(sorted(hosts))}")
        return 0
    tasks = selected_tasks(data)
    if not tasks:
        print("No registered launchd tasks match this Mac user and host.")
        return 0
    if MODE == "list":
        print("Registered launchd tasks")
        for task in tasks:
            display_task(task)
        return 0
    if MODE == "status":
        for task in tasks:
            status_task(task)
        return 0
    if MODE in {"install", "dry-run"}:
        for task in tasks:
            install_task(task, dry_run=MODE == "dry-run")
        return 0
    raise SystemExit(f"Unknown mode: {MODE}")


raise SystemExit(main())
PY
