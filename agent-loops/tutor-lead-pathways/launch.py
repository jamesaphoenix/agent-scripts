#!/usr/bin/env python3
"""Resolve only the required 1Password credentials with bounded CLI reads."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from discovery import atomic_json, now_iso

ALLOWED = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME"}
REFERENCES = {"DATAFORSEO_LOGIN": "op://api-keys/DataForSEO/login",
              "DATAFORSEO_PASSWORD": "op://api-keys/DataForSEO/password"}
OP_HELPER = Path(__file__).resolve().parents[2] / "lib/op-cli.sh"


class CredentialRateLimited(RuntimeError):
    pass


def credential_delay(root):
    path = Path(root) / 'credential-wait.json'
    if not path.exists(): return 0
    return max(0, json.loads(path.read_text())['nextAttemptUnix'] - time.time())


def runner_command(run_directory):
    return ["python3", str(Path(__file__).resolve().with_name("run.py")), "run", "--run-dir", str(run_directory)]


def runner_environment(environment, state_root=None, classification=False):
    if state_root and credential_delay(state_root):
        raise CredentialRateLimited('1Password credential cooldown is active; no read attempted')
    clean = {key: value for key, value in environment.items() if key in ALLOWED}
    credential_environment = dict(clean)
    token = environment.get("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        raise RuntimeError("1Password service account token is unavailable")
    credential_environment["OP_SERVICE_ACCOUNT_TOKEN"] = token
    references = {'OPENAI_API_KEY': 'op://api-keys/OpenAI/api_key'} if classification else REFERENCES
    for key, reference in references.items():
        # Reuse the host's proven launchd/TCC isolation and process-group deadline.
        command = 'source "$1"; OP_CALL_TIMEOUT_SECONDS=20; trap op_cli_cleanup_isolated_home EXIT; run_op read "$2"'
        result = subprocess.run(["/bin/bash", "-c", command, "tutor-credential", str(OP_HELPER), reference],
                                env=credential_environment, capture_output=True, text=True, timeout=50)
        if result.returncode or not result.stdout.strip():
            if 'too many requests' in result.stderr.lower() or 'rate-limit' in result.stderr.lower():
                if state_root:
                    atomic_json(Path(state_root) / 'credential-wait.json', {'observedAt': now_iso(),
                        'nextAttemptUnix': time.time() + 1800, 'reason': '1Password rate limit',
                        'credentialName': key, 'paidRequestSubmitted': False})
                raise CredentialRateLimited('1Password rate-limited credential reads; wait thirty minutes')
            raise RuntimeError("1Password credential read failed: " + key)
        clean[key] = result.stdout.strip()
    if state_root and (Path(state_root) / 'credential-wait.json').exists():
        previous = json.loads((Path(state_root) / 'credential-wait.json').read_text())
        atomic_json(Path(state_root) / 'credential-recovered.json', dict(previous, recoveredAt=now_iso()))
        (Path(state_root) / 'credential-wait.json').unlink()
    return clean


if __name__ == "__main__":
    scripts = Path(__file__).resolve().parent
    root = scripts.parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=root / "state/tutor-lead-pathways-20261011")
    parser.add_argument('--classification', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((args.run_dir / ('job.json' if args.classification else 'manifest.json')).read_text())
    deadline = manifest.get('authorizedDeadline')
    if deadline and datetime.datetime.now(datetime.timezone.utc) >= datetime.datetime.fromisoformat(deadline):
        print('Original campaign deadline reached; no credential read or restart')
        raise SystemExit(0)
    try:
        environment = runner_environment(os.environ, args.run_dir, args.classification)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    command = ([sys.executable, str(scripts / 'qualify_inventory.py'), 'resident', '--run-dir', str(args.run_dir)]
               if args.classification else runner_command(args.run_dir))
    os.execvpe(command[0], command, environment)
