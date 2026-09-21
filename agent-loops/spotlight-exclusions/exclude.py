#!/usr/bin/env python3
"""Keep Spotlight's Privacy exclusion list in sync with config/spotlight-exclusions.json.

Modes:
  --status   (default) no sudo. For each configured path: total indexed items and
             items re-indexed in the last 15 minutes. Non-zero means Spotlight is
             still working on that tree.
  --dry-run  sudo read of the live exclusion list; prints what --live would add.
  --live     sudo: append missing paths, restart mds if anything changed.

The list lives in a root-owned plist (the same one System Settings > Spotlight >
Privacy edits), so --dry-run and --live need a tty for the sudo prompt. Neither
Mac has passwordless sudo and this is deliberately not a launchd job. The script
never removes an exclusion.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "spotlight-exclusions.json"
MACHINES_REGISTRY = ROOT / "launchd-tasks" / "registry.json"
PLIST = "/System/Volumes/Data/.Spotlight-V100/VolumeConfiguration.plist"
MDS_SERVICE = "system/com.apple.metadata.mds"
RECENT_SECONDS = 900


def run_text(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def host_candidates() -> set[str]:
    names = {
        run_text(["hostname"]),
        run_text(["hostname", "-s"]),
        run_text(["scutil", "--get", "LocalHostName"]),
        run_text(["scutil", "--get", "ComputerName"]),
    }
    cleaned = {n for n in names if n}
    return cleaned | {f"{n}.local" for n in cleaned if not n.endswith(".local")}


def current_machine_key() -> str:
    """Same resolution as scripts/install-launchd-tasks.sh: user first, then hostname."""
    data = json.loads(MACHINES_REGISTRY.read_text())
    user = run_text(["id", "-un"])
    hosts = host_candidates()
    for key, machine in (data.get("machines") or {}).items():
        if machine.get("user") == user or hosts & set(machine.get("hosts") or []):
            return key
    sys.exit(f"ERROR: cannot map user={user!r} hosts={sorted(hosts)} to a machine in {MACHINES_REGISTRY}")


def configured_paths(machine_key: str) -> list[str]:
    data = json.loads(CONFIG.read_text())
    entry = (data.get("machines") or {}).get(machine_key)
    if entry is None:
        sys.exit(f"ERROR: no '{machine_key}' block in {CONFIG}")
    out: list[str] = []
    for raw in entry.get("paths") or []:
        p = os.path.expanduser(raw).rstrip("/")
        if not os.path.isabs(p):
            sys.exit(f"ERROR: path must be absolute or ~-relative: {raw}")
        out.append(p)
    return out


def mdfind_count(path: str, query: str) -> int | None:
    if not os.path.isdir(path):
        return None
    out = run_text(["mdfind", "-onlyin", path, "-count", query])
    try:
        return int(out.split()[0])
    except (ValueError, IndexError):
        return None


def status(machine_key: str, paths: list[str]) -> None:
    print(f"spotlight exclusions status machine={machine_key} config={CONFIG.relative_to(ROOT)}")
    if not paths:
        print("  no paths configured for this machine")
        return
    print(f"  {'path':<58} {'indexed':>9} {'last15m':>9}  note")
    for p in paths:
        if not os.path.isdir(p):
            print(f"  {p:<58} {'-':>9} {'-':>9}  missing on disk")
            continue
        total = mdfind_count(p, "kMDItemFSContentChangeDate < $time.now")
        recent = mdfind_count(p, f"kMDItemFSContentChangeDate >= $time.now(-{RECENT_SECONDS})")
        if total is None or recent is None:
            note = "mdfind failed"
        elif recent:
            note = "still being indexed"
        elif total == 0:
            note = "not indexed"
        else:
            note = "indexed, quiet"
        # The total query skips items whose change date mds has not stamped yet
        # (Docker.raw is one), so never show fewer total than recent.
        if total is not None and recent is not None:
            total = max(total, recent)
        print(f"  {p:<58} {total if total is not None else '?':>9} {recent if recent is not None else '?':>9}  {note}")
    print("  (a non-zero 'indexed' count after --live falls to 0 as mds purges in the background)")


def ensure_sudo() -> None:
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return
    if not sys.stdin.isatty():
        sys.exit(
            "ERROR: editing the Spotlight exclusion list needs sudo and there is no tty for the password.\n"
            "       Run with a tty, e.g. ssh -t <host> 'python3 .../exclude.py --live'"
        )
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        sys.exit("ERROR: sudo authentication failed")


def current_exclusions() -> list[str]:
    # plutil reports errors on stdout, so inspect stdout and stderr together.
    res = subprocess.run(
        ["sudo", "plutil", "-extract", "Exclusions", "json", "-o", "-", PLIST],
        capture_output=True, text=True, check=False,
    )
    combined = (res.stdout + res.stderr).strip()
    if res.returncode != 0:
        # A fresh volume has no Exclusions key yet; that is an empty list, not an error.
        if "No value at that key path" in combined or "invalid key path" in combined:
            return []
        sys.exit(
            f"ERROR: could not read {PLIST} (plutil exit {res.returncode}):\n  {combined or '(no output)'}\n"
            "  If the message is 'Operation not permitted', sshd lacks Full Disk Access: enable\n"
            "  'Allow full disk access for remote users' under System Settings > General > Sharing >\n"
            "  Remote Login, or run this script from a local terminal on the machine."
        )
    try:
        return [str(x).rstrip("/") for x in json.loads(res.stdout)]
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: unexpected Exclusions payload: {exc}\n  {combined}")


def apply(machine_key: str, paths: list[str], live: bool) -> None:
    mode = "live" if live else "dry-run"
    print(f"spotlight exclusions {mode} machine={machine_key} plist={PLIST}")
    if not paths:
        print("  no paths configured for this machine; nothing to do")
        return
    ensure_sudo()
    existing = current_exclusions()
    print(f"  current list ({len(existing)}):")
    for e in existing:
        print(f"    {e}")
    missing = [p for p in paths if p not in existing]
    skipped_absent = [p for p in missing if not os.path.isdir(p)]
    to_add = [p for p in missing if os.path.isdir(p)]
    for p in skipped_absent:
        print(f"  skip (missing on disk): {p}")
    if not to_add:
        print("  all configured paths already excluded; nothing to do")
        return
    for p in to_add:
        print(f"  {'add' if live else 'would add'}: {p}")
    if not live:
        return
    for p in to_add:
        subprocess.run(["sudo", "defaults", "write", PLIST, "Exclusions", "-array-add", p], check=True)
    after = current_exclusions()
    not_applied = [p for p in to_add if p not in after]
    if not_applied:
        sys.exit(f"ERROR: paths not present after write: {not_applied}")
    print(f"  restarting mds ({MDS_SERVICE}) so the new list takes effect")
    subprocess.run(["sudo", "launchctl", "kickstart", "-k", MDS_SERVICE], check=True)
    print(f"  done: {len(to_add)} added, list now has {len(after)} entries")


def main(argv: list[str]) -> None:
    mode = "status"
    for arg in argv:
        if arg in ("--status", "--dry-run", "--live"):
            mode = arg[2:]
        elif arg in ("-h", "--help"):
            print(__doc__.strip())
            return
        else:
            sys.exit(f"Unknown argument: {arg}\n\n{__doc__.strip()}")
    machine_key = current_machine_key()
    paths = configured_paths(machine_key)
    if mode == "status":
        status(machine_key, paths)
    else:
        apply(machine_key, paths, live=(mode == "live"))


if __name__ == "__main__":
    main(sys.argv[1:])
