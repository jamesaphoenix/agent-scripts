#!/usr/bin/env python3
"""worktree-janitor: keep the MacBook's git worktrees and dev servers under control.

Config: config/worktree-janitor.json. Three rules, in this order every run:

  1. MERGED worktrees are removed. "Merged" means the branch's content is already in a
     target branch (main / staging), established by git ancestry or a no-op
     `git merge-tree` (catches squash and rebase merges). Historical merged PRs do
     not establish that a reused branch tip is merged. Uncommitted changes are archived (diff + untracked files)
     before removal; the worktree's dev servers are reaped; the repo's own remove
     script runs when it has one (drops the Postgres schema too); the local branch is
     deleted.
  2. DEV SERVERS older than servers.maxAgeHours that run from any worktree are killed:
     api, worker, web and their helpers, node family only. Infra is never touched.
  3. IDLE, UNMERGED worktrees (no commit and no uncommitted-file change for
     idleAlert.idleDays) are emailed as a digest via Resend, at most once a day and
     only when the list changes, with a weekly reminder otherwise.

Usage:
  janitor.py                 do it
  janitor.py --dry-run       report what would happen, change nothing, send nothing
  janitor.py --status        print the last report
  janitor.py --no-email      run rules 1 and 2, skip the digest
  janitor.py --json          machine-readable report on stdout
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONFIG_PATH = Path(os.environ.get("WORKTREE_JANITOR_CONFIG", ROOT / "config" / "worktree-janitor.json"))
STATE_DIR = Path(os.environ.get("WORKTREE_JANITOR_STATE_DIR", ROOT / "state" / "worktree-janitor"))
ARCHIVE_DIR = STATE_DIR / "archive"
LOG_PATH = STATE_DIR / "janitor.log"
REPORT_PATH = STATE_DIR / "last-report.json"
DIGEST_STATE_PATH = STATE_DIR / "digest-state.json"
NODE_FAMILY = re.compile(r"(^|/)(node|tsx|bun|vite|wrangler|workerd|esbuild|next)(\s|$)|\b(tsx|vite|wrangler|workerd|esbuild)\b")

NOW = time.time()


def log(msg: str) -> None:
    line = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} worktree-janitor: {msg}"
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s: {' '.join(cmd[:3])}"
    except OSError as exc:
        return 127, "", str(exc)


def git(repo: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    code, out, _ = run(["git", "-C", str(repo), *args], timeout=timeout)
    return code, out.strip()


def keychain(service: str) -> str:
    code, out, _ = run(["security", "find-generic-password", "-s", service, "-w"])
    return out.strip() if code == 0 else ""


def human_size(path: Path) -> str:
    code, out, _ = run(["du", "-sh", str(path)], timeout=600)
    return out.split("\t")[0] if code == 0 and out else "?"


# ---------------------------------------------------------------- worktrees ---------

def list_worktrees(repo: Path) -> list[dict[str, Any]]:
    code, out = git(repo, "worktree", "list", "--porcelain")
    if code != 0:
        return []
    items: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.splitlines() + [""]:
        if not line:
            if current:
                items.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current = {"path": value, "branch": None, "detached": False, "locked": False, "prunable": False}
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key == "locked":
            current["locked"] = True
        elif key == "prunable":
            current["prunable"] = True
    return items


def merged_prs_by_head(repo: Path) -> dict[str, str]:
    code, out, _ = run(
        ["gh", "pr", "list", "--state", "merged", "--limit", "500", "--json", "headRefName,number,baseRefName,mergedAt"],
        cwd=repo, timeout=90,
    )
    if code != 0:
        log(f"gh pr list failed in {repo.name}; GitHub signal unavailable this run")
        return {}
    result: dict[str, str] = {}
    for pr in json.loads(out or "[]"):
        head = pr["headRefName"]
        if head not in result:
            result[head] = f"PR #{pr['number']} -> {pr['baseRefName']} {pr['mergedAt'][:10]}"
    return result


def merged_signals(repo: Path, branch: str, targets: list[str], prs: dict[str, str]) -> list[str]:
    signals: list[str] = []
    for target in targets:
        ref = f"origin/{target}"
        if git(repo, "rev-parse", "--verify", "-q", ref)[0] != 0:
            continue
        if git(repo, "merge-base", "--is-ancestor", branch, ref)[0] == 0:
            signals.append(f"ancestor of {target}")
            continue
        # Would merging the branch into the target change anything? If the result tree
        # equals the target's tree, the branch's content is already there (squash/rebase).
        code, tree = git(repo, "merge-tree", "--write-tree", ref, branch, timeout=120)
        if code == 0 and tree.splitlines():
            target_tree = git(repo, "rev-parse", f"{ref}^{{tree}}")[1]
            if tree.splitlines()[0].strip() == target_tree:
                signals.append(f"content already in {target}")
    # A merged PR title/branch name is historical, not proof the current branch
    # tip is merged. Reused branches can contain new work after the old PR.
    return signals


def worktree_activity(repo: Path, wt: Path, branch: str | None) -> tuple[float, int, list[str]]:
    """Return (last activity epoch, dirty file count, dirty file list)."""
    last = 0.0
    code, out = git(wt, "log", "-1", "--format=%ct", "HEAD")
    if code == 0 and out:
        last = float(out)
    status_code, out = git(wt, "status", "--porcelain", "--untracked-files=all", timeout=120)
    dirty = [line[3:] for line in out.splitlines() if line.strip()] if status_code == 0 else []
    for rel in dirty[:500]:
        try:
            last = max(last, (wt / rel).stat().st_mtime)
        except OSError:
            continue
    return last, len(dirty), dirty


def archive_dirty(repo_id: str, wt: Path, name: str, dirty: list[str], dry_run: bool) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = ARCHIVE_DIR / repo_id / f"{name.replace('/', '__')}-{stamp}"
    if dry_run:
        return str(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    code, diff = git(wt, "diff", "--binary", "HEAD", timeout=120)
    if code == 0 and diff:
        base.with_suffix(".patch").write_text(diff + "\n", encoding="utf-8")
    code, untracked = git(wt, "ls-files", "--others", "--exclude-standard", timeout=120)
    files = [f for f in untracked.splitlines() if f]
    if files:
        with tarfile.open(base.with_suffix(".untracked.tar.gz"), "w:gz") as tar:
            for rel in files:
                try:
                    tar.add(wt / rel, arcname=rel, recursive=False)
                except OSError:
                    continue
    base.with_suffix(".files.txt").write_text("\n".join(dirty) + "\n", encoding="utf-8")
    return str(base)


# ---------------------------------------------------------------- processes ---------

def parse_etime(text: str) -> float:
    """[[dd-]hh:]mm:ss -> seconds."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return days * 86400 + h * 3600 + m * 60 + s


def process_cwds() -> dict[int, str]:
    code, out, _ = run(["lsof", "-d", "cwd", "-Fpn"], timeout=120)
    cwds: dict[int, str] = {}
    pid = 0
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid:
            cwds[pid] = line[1:]
    return cwds


def classify(args: str, cwd: str) -> str | None:
    text = f"{cwd} {args}"
    if "apps/api" in text or "src/server.ts" in args:
        return "api"
    if "apps/worker" in text or "/worker/src" in args:
        return "worker"
    if re.search(r"vite|wrangler|workerd|/next\b|apps/web|apps/docs", text):
        return "web"
    if re.search(r"esbuild|tsx/dist/cli\.mjs watch|tsx watch", args):
        return "helper"
    return None


def list_dev_servers(worktrees: dict[str, tuple[str, str]], never: list[str]) -> list[dict[str, Any]]:
    """worktrees: path -> (repo_id, name). Returns node-family processes inside any worktree."""
    code, out, _ = run(["ps", "-axo", "pid=,etime=,rss=,args="], timeout=30)
    if code != 0:
        return []
    cwds = process_cwds()
    servers: list[dict[str, Any]] = []
    roots = sorted(worktrees, key=len, reverse=True)
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, etime, rss, args = int(parts[0]), parts[1], int(parts[2]), parts[3]
        if pid == os.getpid() or not NODE_FAMILY.search(args):
            continue
        if any(p.lower() in args.lower() for p in never):
            continue
        cwd = cwds.get(pid, "")
        home = str(Path.home())
        owner = next((r for r in roots if cwd == r or cwd.startswith(r + "/") or (r + "/") in args), None)
        if not owner:
            continue
        cls = classify(args, cwd)
        if not cls:
            continue
        repo_id, name = worktrees[owner]
        servers.append({
            "pid": pid, "class": cls, "ageHours": round(parse_etime(etime) / 3600, 1), "rssMB": rss // 1024,
            "repo": repo_id, "worktree": name, "cwd": cwd.replace(home, "~"),
            "args": re.sub(r"\s+", " ", args.replace(home, "~"))[:160],
        })
    return servers


def kill_pids(pids: list[int], dry_run: bool) -> None:
    if dry_run or not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(2)
    for pid in pids:
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------- removal -----------

def remove_worktree(repo: dict[str, Any], repo_path: Path, wt: dict[str, Any], dry_run: bool) -> bool:
    path = Path(wt["path"])
    name = path.name
    remove_cmd = repo.get("removeCommand")
    # The repo's own script only knows <root>/.worktrees/<name>; use it there, else generic.
    use_repo_script = bool(remove_cmd) and path.parent == repo_path / ".worktrees" and "/" not in name
    if dry_run:
        return True
    if use_repo_script:
        cmd = [c.replace("{name}", name) for c in remove_cmd]
        cmd[0] = str(repo_path / cmd[0]) if not cmd[0].startswith("/") else cmd[0]
        code, _, err = run(cmd, cwd=repo_path, timeout=600)
        if code != 0 or path.exists():
            log(f"[{repo['id']}] repo remove script failed for {name} (exit {code}); falling back: {err.strip()[-200:]}")
        else:
            return True
    git(repo_path, "worktree", "remove", "--force", str(path), timeout=300)
    if path.exists():
        run(["rm", "-rf", str(path)], timeout=900)
    git(repo_path, "worktree", "prune")
    return not path.exists()


# ---------------------------------------------------------------- digest ------------

def send_digest(cfg: dict[str, Any], idle: list[dict[str, Any]], removed: list[dict[str, Any]],
                killed: list[dict[str, Any]], dry_run: bool) -> str:
    alert = cfg["idleAlert"]
    state: dict[str, Any] = {}
    if DIGEST_STATE_PATH.exists():
        try:
            state = json.loads(DIGEST_STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    key = hashlib.sha256(json.dumps(sorted(f"{i['repo']}/{i['worktree']}" for i in idle)).encode()).hexdigest()
    last_sent = float(state.get("lastSentAt", 0))
    hours_since = (NOW - last_sent) / 3600
    changed = state.get("listHash") != key
    if not idle:
        return "nothing idle; no digest"
    if hours_since < alert["minHoursBetweenDigests"]:
        return f"digest sent {hours_since:.0f}h ago; waiting"
    if not changed and hours_since < alert["remindEveryDays"] * 24:
        return "idle list unchanged; next reminder later"

    lines = [f"{len(idle)} worktree(s) on the MacBook Pro have had no commit or file change for "
             f"{alert['idleDays']}+ days and are NOT merged into main/staging. Decide, then remove:", ""]
    for i in sorted(idle, key=lambda x: -x["idleDays"]):
        lines.append(f"- {i['repo']} / {i['worktree']}  (branch {i['branch']}, idle {i['idleDays']}d, "
                     f"{i['dirty']} uncommitted, {i['size']}{', servers: ' + str(i['servers']) if i['servers'] else ''})")
        lines.append(f"    {i['removeHint']}")
    if removed or killed:
        lines += ["", f"Also in the last run: removed {len(removed)} merged worktree(s), killed {len(killed)} dev server(s) (from merged worktrees or older than the age limit)."]
    lines += ["", f"Report: {REPORT_PATH}", "Sent by agent-scripts/agent-loops/worktree-janitor (daily launchd job on the MacBook Pro)."]
    body = "\n".join(lines)
    if dry_run:
        return f"would send digest ({len(idle)} idle) to {alert['to']}:\n" + body
    api_key = keychain(alert["resendKeychainService"])
    if not api_key:
        return f"Resend key missing from keychain service {alert['resendKeychainService']}; digest not sent"
    payload = json.dumps({"from": alert["from"], "to": [alert["to"]], "subject": alert["subject"], "text": body}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=payload, method="POST",
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                                          # Cloudflare in front of api.resend.com rejects Python-urllib's default UA (403, code 1010).
                                          "User-Agent": "worktree-janitor/1 (agent-scripts; +https://github.com/Just-Understanding-Data-Ltd)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        return f"Resend rejected the digest: {exc.code} {exc.read().decode(errors='replace')[:200]}"
    except urllib.error.URLError as exc:
        return f"Resend unreachable: {exc.reason}"
    DIGEST_STATE_PATH.write_text(json.dumps({"lastSentAt": NOW, "listHash": key, "count": len(idle)}), encoding="utf-8")
    return f"digest sent to {alert['to']} ({len(idle)} idle worktrees)"


# ---------------------------------------------------------------- main --------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--json", action="store_true")
    opts = ap.parse_args()

    if opts.status:
        print(REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else "no report yet")
        return 0

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    dry = opts.dry_run
    log(f"run start{' (DRY RUN)' if dry else ''}")

    report: dict[str, Any] = {"ranAt": dt.datetime.now(dt.timezone.utc).isoformat(), "dryRun": dry,
                              "removed": [], "killed": [], "idle": [], "kept": [], "errors": []}
    all_worktrees: dict[str, tuple[str, str]] = {}
    per_repo: list[tuple[dict[str, Any], Path, list[dict[str, Any]]]] = []

    # Pass 1: inventory and classification.
    for repo in cfg["repos"]:
        repo_path = Path(os.path.expanduser(repo["path"]))
        if not (repo_path / ".git").exists():
            report["errors"].append(f"{repo['id']}: not a git repo at {repo_path}")
            continue
        code, _ = git(repo_path, "fetch", "--prune", "--quiet", "origin", timeout=120)
        if code != 0:
            log(f"[{repo['id']}] fetch failed; using last known remote refs")
        prs = merged_prs_by_head(repo_path)
        entries: list[dict[str, Any]] = []
        for wt in list_worktrees(repo_path):
            path = Path(wt["path"])
            if path == repo_path:
                continue
            if wt["prunable"] or not path.exists():
                if not dry:
                    git(repo_path, "worktree", "prune")
                log(f"[{repo['id']}] pruned dangling worktree record {path.name}")
                continue
            name = str(path.relative_to(repo_path.parent)) if path.is_relative_to(repo_path.parent) else path.name
            for prefix in ("/.worktrees/", "/worktrees/", "/.claude/worktrees/"):
                name = name.split(prefix)[-1]
            for prefix in (".worktrees/", "worktrees/", ".claude/worktrees/"):
                name = name.removeprefix(prefix)
            all_worktrees[str(path)] = (repo["id"], name)
            last, dirty_n, dirty = worktree_activity(repo_path, path, wt["branch"])
            dirty_newest = 0.0
            for rel in dirty[:500]:
                try:
                    dirty_newest = max(dirty_newest, (path / rel).stat().st_mtime)
                except OSError:
                    continue
            try:
                created = (path / ".git").stat().st_mtime
            except OSError:
                created = 0.0
            is_target = wt["branch"] in repo["targets"]
            signals = [] if wt["detached"] or wt["locked"] or is_target else merged_signals(repo_path, wt["branch"], repo["targets"], prs)
            grace = cfg.get("removal", {})
            hold = ""
            if signals and dirty_n and dirty_newest and (NOW - dirty_newest) < grace.get("dirtyGraceDays", 3) * 86400:
                hold = f"uncommitted changes touched {int((NOW - dirty_newest) / 3600)}h ago"
            elif signals and created and (NOW - created) < grace.get("minAgeHours", 24) * 3600:
                hold = f"worktree created {int((NOW - created) / 3600)}h ago"
            entries.append({**wt, "name": name, "repo": repo["id"], "signals": signals, "hold": hold, "dirty": dirty_n,
                            "dirtyFiles": dirty, "isTarget": is_target,
                            "idleDays": int((NOW - last) / 86400) if last else 9999})
        per_repo.append((repo, repo_path, entries))

    servers = list_dev_servers(all_worktrees, cfg["servers"]["neverKillPatterns"])

    # Rule 1: merged worktrees.
    for repo, repo_path, entries in per_repo:
        for e in entries:
            if not e["signals"]:
                continue
            if e["hold"]:
                log(f"[{repo['id']}] keeping merged worktree {e['name']} for now: {e['hold']}")
                report["kept"].append({"repo": repo["id"], "worktree": e["name"], "branch": e["branch"],
                                       "idleDays": e["idleDays"], "dirty": e["dirty"], "locked": e["locked"], "hold": e["hold"]})
                continue
            path = Path(e["path"])
            size = human_size(path)
            archive = archive_dirty(repo["id"], path, e["name"], e["dirtyFiles"], dry) if e["dirty"] else ""
            pids = [s["pid"] for s in servers if s["repo"] == repo["id"] and s["worktree"] == e["name"]]
            kill_pids(pids, dry)
            ok = remove_worktree(repo, repo_path, e, dry)
            if ok and e["branch"] and not dry:
                git(repo_path, "branch", "-D", e["branch"])
            rec = {"repo": repo["id"], "worktree": e["name"], "branch": e["branch"], "size": size,
                   "signals": e["signals"], "dirty": e["dirty"], "archive": archive, "serversKilled": pids, "ok": ok}
            report["removed"].append(rec)
            log(f"[{repo['id']}] {'would remove' if dry else ('removed' if ok else 'FAILED to remove')} {e['name']} "
                f"({size}, {'; '.join(e['signals'])}{', ' + str(e['dirty']) + ' dirty archived' if e['dirty'] else ''}"
                f"{', killed ' + str(len(pids)) + ' server(s)' if pids else ''})")
            for s in servers:
                if s["pid"] in pids:
                    s["killedBy"] = "merged"

    # Rule 2: stale dev servers.
    max_age = cfg["servers"]["maxAgeHours"]
    classes = set(cfg["servers"]["killClasses"])
    stale = [s for s in servers if "killedBy" not in s and s["class"] in classes and s["ageHours"] >= max_age]
    kill_pids([s["pid"] for s in stale], dry)
    for s in stale:
        s["killedBy"] = "age"
        log(f"{'would kill' if dry else 'killed'} {s['class']} pid {s['pid']} ({s['ageHours']}h, {s['rssMB']}MB) in {s['repo']}/{s['worktree']}")
    report["killed"] = [s for s in servers if s.get("killedBy")]

    # Rule 3: idle, unmerged worktrees -> digest.
    idle_days = cfg["idleAlert"]["idleDays"]
    live_servers = {(s["repo"], s["worktree"]) for s in servers if not s.get("killedBy")}
    for repo, repo_path, entries in per_repo:
        for e in entries:
            if e["signals"] or e["isTarget"]:
                continue
            item = {"repo": repo["id"], "worktree": e["name"], "branch": e["branch"] or "(detached)",
                    "idleDays": e["idleDays"], "dirty": e["dirty"], "locked": e["locked"]}
            if e["idleDays"] >= idle_days:
                remove_cmd = repo.get("removeCommand") or []
                hint = (f"cd {repo['path']} && {' '.join(c.replace('{name}', e['name']) for c in remove_cmd)}"
                        if remove_cmd and "/" not in e["name"]
                        else f"git -C {repo['path']} worktree remove --force {e['path'].replace(str(Path.home()), '~')}")
                item.update({"size": human_size(Path(e["path"])), "servers": len([1 for k in live_servers if k == (repo["id"], e["name"])]),
                             "removeHint": hint})
                report["idle"].append(item)
            else:
                report["kept"].append(item)

    digest_result = "skipped (--no-email)" if opts.no_email else send_digest(cfg, report["idle"], report["removed"], report["killed"], dry)
    report["digest"] = digest_result
    log(f"digest: {digest_result.splitlines()[0]}")

    freed = sum(1 for r in report["removed"] if r["ok"])
    log(f"run done: {freed} merged worktree(s) {'would be ' if dry else ''}removed, "
        f"{len([s for s in report['killed'] if s.get('killedBy') == 'age'])} stale server(s) {'would be ' if dry else ''}killed, "
        f"{len(report['idle'])} idle for digest, {len(report['kept'])} active kept")
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if opts.json:
        print(json.dumps(report, indent=2))
    elif dry and digest_result.startswith("would send"):
        print("\n" + digest_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
