#!/usr/bin/env python3
"""Resolve only the required 1Password credentials with bounded CLI reads."""
import os
from pathlib import Path
import subprocess
import sys

ALLOWED = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME"}
REFERENCES = {"DATAFORSEO_LOGIN": "op://api-keys/DataForSEO/login",
              "DATAFORSEO_PASSWORD": "op://api-keys/DataForSEO/password"}
OP_HELPER = Path(__file__).resolve().parents[2] / "lib/op-cli.sh"


def runner_environment(environment):
    clean = {key: value for key, value in environment.items() if key in ALLOWED}
    credential_environment = dict(clean)
    token = environment.get("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        raise RuntimeError("1Password service account token is unavailable")
    credential_environment["OP_SERVICE_ACCOUNT_TOKEN"] = token
    for key, reference in REFERENCES.items():
        # Reuse the host's proven launchd/TCC isolation and process-group deadline.
        command = 'source "$1"; OP_CALL_TIMEOUT_SECONDS=20; trap op_cli_cleanup_isolated_home EXIT; run_op read "$2"'
        result = subprocess.run(["/bin/bash", "-c", command, "tutor-credential", str(OP_HELPER), reference],
                                env=credential_environment, capture_output=True, text=True, timeout=50)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError("1Password credential read failed: " + key)
        clean[key] = result.stdout.strip()
    return clean


if __name__ == "__main__":
    scripts = Path(__file__).resolve().parent
    root = scripts.parent.parent
    try:
        environment = runner_environment(os.environ)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    os.execvpe("python3", ["python3", str(scripts / "run.py"), "run", "--run-dir",
                           str(root / "state/tutor-lead-pathways-20261011")], environment)
