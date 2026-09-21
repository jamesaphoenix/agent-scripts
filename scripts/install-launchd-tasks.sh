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
  scripts/install-launchd-tasks.sh                  install every task registered for THIS machine
  scripts/install-launchd-tasks.sh --dry-run
  scripts/install-launchd-tasks.sh --status         loaded / last exit for this machine's tasks
  scripts/install-launchd-tasks.sh --audit          registered-but-missing and installed-but-unregistered jobs
  scripts/install-launchd-tasks.sh --list           tasks registered for this machine
  scripts/install-launchd-tasks.sh --all-hosts --list
  scripts/install-launchd-tasks.sh --table          markdown table of every machine's tasks (for launchd-tasks/README.md)
  scripts/install-launchd-tasks.sh --task TASK_ID [--dry-run|--status]

launchd-tasks/registry.json is the single source of truth for which jobs run on
which Mac. Each task names a `machine` (see the registry's `machines` block); the
same script runs on both Macs and only acts on tasks for the machine it is on.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install) MODE="install"; shift ;;
    --dry-run) MODE="dry-run"; shift ;;
    --status) MODE="status"; shift ;;
    --audit) MODE="audit"; shift ;;
    --list) MODE="list"; shift ;;
    --table) MODE="table"; shift ;;
    --task) TASK_ID="$2"; shift 2 ;;
    --all-hosts) ALL_HOSTS="1"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# Cross-host inventory is safe; installing another host's tasks locally is not.
if [ "$ALL_HOSTS" = "1" ] && [ "$MODE" != "list" ] && [ "$MODE" != "table" ]; then
  echo "--all-hosts is supported only with --list or --table" >&2
  exit 2
fi

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

# LaunchAgents with these label prefixes are "ours"; anything else in
# ~/Library/LaunchAgents is a vendor updater and ignored by --audit.
OWN_LABEL_PREFIXES = ("com.jud.", "com.octospark.", "com.jamesphoenix.")


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


def machines(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return data.get("machines") or {}


def resolve_machine(data: dict[str, Any], task: dict[str, Any]) -> tuple[set[str], set[str], str]:
    """Return (hosts, users, machine display name) for a task.

    A task names a `machine` key from the registry's `machines` block. Explicit
    `hosts` / `users` on the task are still honoured for one-off overrides.
    """
    machine_key = task.get("machine")
    machine = machines(data).get(machine_key or "", {})
    hosts = set(task.get("hosts") or machine.get("hosts") or [])
    users = set(task.get("users") or ([machine["user"]] if machine.get("user") else []))
    name = machine.get("name") or machine_key or "(any)"
    return hosts, users, name


def current_machine_key(data: dict[str, Any]) -> str:
    user = run_text(["id", "-un"])
    hosts = host_candidates()
    for key, machine in machines(data).items():
        if hosts & set(machine.get("hosts") or []) and (not machine.get("user") or machine["user"] == user):
            return key
    return ""


def task_matches_current_host(data: dict[str, Any], task: dict[str, Any], user: str, hosts: set[str]) -> bool:
    if TASK_ID and task.get("id") != TASK_ID:
        return False
    if ALL_HOSTS:
        return True
    task_hosts, users, _ = resolve_machine(data, task)
    return (not users or user in users) and (not task_hosts or bool(task_hosts & hosts))


def selected_tasks(data: dict[str, Any]) -> list[dict[str, Any]]:
    user = run_text(["id", "-un"])
    hosts = host_candidates()
    tasks = [
        task
        for task in data.get("tasks", [])
        if task.get("enabled", True) and task_matches_current_host(data, task, user, hosts)
    ]
    if TASK_ID and not tasks:
        raise SystemExit(f"No enabled task matched {TASK_ID!r} for user {user} on hosts {sorted(hosts)}")
    return tasks


def display_task(data: dict[str, Any], task: dict[str, Any]) -> None:
    hosts, users, machine = resolve_machine(data, task)
    print(f"[{task['id']}] {task.get('name', task['id'])}")
    print(f"  label: {task.get('launchdLabel', '(none)')}")
    print(f"  schedule: {task.get('schedule', '(unspecified)')}")
    print(f"  machine: {machine}")
    print(f"  hosts: {', '.join(sorted(hosts)) or 'any'}")
    print(f"  users: {', '.join(sorted(users)) or 'any'}")
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
    elif kind == "external":
        # Registered for fleet visibility and --audit only; another repo owns install.
        print(f"  external: managed by {task['install'].get('managedBy', 'another repo')} - nothing to do here")
    else:
        raise SystemExit(f"Unknown install kind for task {task['id']}: {kind}")


def launchctl_list() -> dict[str, tuple[str, str]]:
    """label -> (pid, last exit) from `launchctl list`."""
    out = run_text(["launchctl", "list"])
    result: dict[str, tuple[str, str]] = {}
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            result[parts[2]] = (parts[0], parts[1])
    return result


def status_task(task: dict[str, Any], loaded: dict[str, tuple[str, str]]) -> None:
    label = task.get("launchdLabel")
    print(f"[{task['id']}] {task.get('name', task['id'])}")
    if not label:
        print("  launchd: no label")
        return
    uid = os.getuid()
    print(f"  launchd: {'loaded' if label in loaded else 'not loaded'} ({label})")
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


def audit(data: dict[str, Any], tasks: list[dict[str, Any]]) -> int:
    """Compare the registry with what is actually in ~/Library/LaunchAgents."""
    loaded = launchctl_list()
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    installed_own = {
        p.stem for p in plist_dir.glob("*.plist") if p.stem.startswith(OWN_LABEL_PREFIXES)
    }
    # Labels registered for this machine, plus extra labels a task declares it owns
    # (e.g. the auto-fix runner task installs two agents under one registry entry).
    expected: dict[str, str] = {}
    for task in tasks:
        for label in [task.get("launchdLabel")] + list(task.get("extraLaunchdLabels") or []):
            if label:
                expected[label] = task["id"]
    machine = current_machine_key(data) or "(unknown machine)"
    problems = 0
    print(f"launchd audit for machine '{machine}' as {run_text(['id', '-un'])}")
    print()
    print("registered for this machine:")
    for label, task_id in sorted(expected.items()):
        state = "loaded" if label in loaded else ("plist present, NOT loaded" if label in installed_own else "MISSING")
        if label in loaded:
            pid, code = loaded[label]
            state += f" (pid {pid}, last exit {code})"
            if code not in ("0", "-"):
                problems += 1
                state += "  <- non-zero exit"
        else:
            problems += 1
        print(f"  {label:50s} {state}   [{task_id}]")
    unregistered = sorted(installed_own - set(expected))
    print()
    if unregistered:
        problems += len(unregistered)
        print("installed here but NOT registered for this machine (stray or belongs elsewhere):")
        for label in unregistered:
            owner = next(
                (
                    resolve_machine(data, t)[2]
                    for t in data.get("tasks", [])
                    if label in [t.get("launchdLabel")] + list(t.get("extraLaunchdLabels") or [])
                ),
                None,
            )
            hint = f"registered for {owner}" if owner else "not in registry at all"
            print(f"  {label:50s} {hint}")
    else:
        print("no unregistered com.jud / com.octospark / com.jamesphoenix agents installed here.")
    print()
    print("ok" if problems == 0 else f"{problems} problem(s)")
    return 0 if problems == 0 else 1


def table(data: dict[str, Any]) -> None:
    by_machine: dict[str, list[dict[str, Any]]] = {}
    for task in data.get("tasks", []):
        _, _, name = resolve_machine(data, task)
        by_machine.setdefault(name, []).append(task)
    for name in sorted(by_machine):
        print(f"### {name}")
        print()
        print("| Task | launchd label | Schedule | Purpose |")
        print("|---|---|---|---|")
        for task in by_machine[name]:
            flag = "" if task.get("enabled", True) else " (disabled)"
            purpose = task.get("name", task["id"])
            print(f"| `{task['id']}`{flag} | `{task.get('launchdLabel', '')}` | {task.get('schedule', '')} | {purpose} |")
        print()


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if MODE == "table":
        table(data)
        return 0
    tasks = selected_tasks(data)
    if MODE == "audit":
        return audit(data, tasks)
    if not tasks:
        print("No registered launchd tasks match this Mac user and host.")
        print(f"Detected hosts: {', '.join(sorted(host_candidates()))}")
        return 0
    if MODE == "list":
        print("Registered launchd tasks")
        for task in tasks:
            display_task(data, task)
        return 0
    if MODE == "status":
        loaded = launchctl_list()
        for task in tasks:
            status_task(task, loaded)
        return 0
    if MODE in {"install", "dry-run"}:
        for task in tasks:
            install_task(task, dry_run=MODE == "dry-run")
        return 0
    raise SystemExit(f"Unknown mode: {MODE}")


raise SystemExit(main())
PY
