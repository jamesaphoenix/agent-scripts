#!/usr/bin/env python3
"""Report drift between files that trace-learn and octospark keep identical by hand-porting.

The list of expected-identical paths lives in config/cross-repo-identical-files.txt.
Both repos are read at a git ref (default origin/staging) with `git cat-file`, so the
working trees are never touched. A path suffixed with `  # comments` is compared with
comments and punctuation stripped, for files whose only difference is comment wording.

Usage:
  scripts/cross-repo-drift.py [--ref origin/staging] [--fetch] [--json]
      [--trace-learn DIR] [--octospark DIR] [--list FILE]

Exit status: 0 when every listed file matches, 1 when any has drifted or is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_LIST = HERE.parent / "config" / "cross-repo-identical-files.txt"
DEFAULT_ROOT = Path(
    os.environ.get("JUD_PROJECTS_DIR", str(Path.home() / "Desktop/projects/just-understanding-data"))
)
COMMENT_MARKER = "# comments"


def parse_list(text: str) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        comments_only = line.endswith(COMMENT_MARKER)
        path = line[: -len(COMMENT_MARKER)].strip() if comments_only else line
        entries.append((path, comments_only))
    return entries


def strip_comments(text: str, path: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("*"):
            continue
        if path.endswith(".sh") and line.startswith("#") and not line.startswith("#!"):
            continue
        line = re.sub(r"\s//\s.*$", "", line)
        if path.endswith(".sh"):
            line = re.sub(r"\s#\s.*$", "", line)
        line = re.sub(r"[\'\"`;,\s]", "", line)
        if line:
            kept.append(line)
    return "\n".join(kept)


def read_at_ref(repo: Path, ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def compare(entries, read_a, read_b) -> list[dict]:
    """Return one finding per drifted or missing path. read_a/read_b map path -> text or None."""
    findings = []
    for path, comments_only in entries:
        a, b = read_a(path), read_b(path)
        if a is None or b is None:
            missing = [name for name, text in (("trace-learn", a), ("octospark", b)) if text is None]
            findings.append({"path": path, "status": "missing", "missing_in": missing})
            continue
        same = strip_comments(a, path) == strip_comments(b, path) if comments_only else a == b
        if not same:
            findings.append({"path": path, "status": "drifted", "comments_only": comments_only})
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="origin/staging")
    parser.add_argument("--trace-learn", type=Path, default=DEFAULT_ROOT / "trace-learn")
    parser.add_argument("--octospark", type=Path, default=DEFAULT_ROOT / "octospark")
    parser.add_argument("--list", type=Path, default=DEFAULT_LIST)
    parser.add_argument("--fetch", action="store_true", help="git fetch the ref's remote in both repos first")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.fetch:
        remote, _, branch = args.ref.partition("/")
        for repo in (args.trace_learn, args.octospark):
            subprocess.run(["git", "-C", str(repo), "fetch", "-q", remote, branch], check=True)

    entries = parse_list(args.list.read_text())
    findings = compare(
        entries,
        lambda p: read_at_ref(args.trace_learn, args.ref, p),
        lambda p: read_at_ref(args.octospark, args.ref, p),
    )

    if args.json:
        print(json.dumps({"ref": args.ref, "checked": len(entries), "findings": findings}, indent=2))
    else:
        print(f"Checked {len(entries)} files at {args.ref}: {len(findings)} drifted or missing.")
        for f in findings:
            detail = f"missing in {', '.join(f['missing_in'])}" if f["status"] == "missing" else "contents differ"
            print(f"  {f['path']}: {detail}")
        if findings:
            print("Port the change to the other repo, or remove the path from the list if it should diverge.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
