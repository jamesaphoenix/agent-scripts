#!/usr/bin/env python3
"""Resolve only the required 1Password credentials with bounded CLI reads."""
import argparse
import datetime
import fcntl
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
CURRENT_CLASSIFICATION_STAGE = 'tutor-qualification-20261018-free-10h'
CURRENT_RESET = datetime.datetime.fromisoformat('2026-10-18T15:22:19.907036+00:00')


class CredentialRateLimited(RuntimeError):
    pass


def credential_delay(root):
    path = Path(root) / 'credential-wait.json'
    if not path.exists(): return 0
    return max(0, json.loads(path.read_text())['nextAttemptUnix'] - time.time())


def runner_command(run_directory):
    return ["python3", str(Path(__file__).resolve().with_name("run.py")), "run", "--run-dir", str(run_directory)]


def checked_quota(root):
    """Require one post-reset account-quota observation before this window reads a key."""
    path = root / 'credential-quota-check.json'
    if not path.exists():
        raise CredentialRateLimited('Awaiting one account-quota check after the recorded reset; no secret read attempted')
    report = json.loads(path.read_text())
    observed = datetime.datetime.fromisoformat(report['observedAt'].replace('Z', '+00:00'))
    if observed < CURRENT_RESET or report.get('status') != 'available' or report.get('accountReadWriteRemaining', 0) < 1:
        raise CredentialRateLimited('Account quota unavailable for this window; no secret read attempted')


def cached_classification_key(root, read_key):
    """Read the Decisions key once per task, then reuse a private file across restarts."""
    scratch = root / 'credential-scratch'
    scratch.mkdir(mode=0o700, exist_ok=True)
    if scratch.is_symlink() or scratch.stat().st_mode & 0o077:
        raise RuntimeError('Credential scratch directory is not private')
    lock = scratch / 'key.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        cache = scratch / 'openai-api-key'
        if cache.exists():
            if cache.is_symlink() or cache.stat().st_mode & 0o077:
                raise RuntimeError('Credential cache is not private')
            value = cache.read_text().strip()
            if not value:
                raise RuntimeError('Credential cache is empty')
            return value
        checked_quota(root)
        attempt = scratch / 'read-attempt.json'
        try:
            attempt_fd = os.open(attempt, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise RuntimeError('Credential read already attempted without a valid task cache; no retry') from None
        with os.fdopen(attempt_fd, 'w') as stream:
            json.dump({'attemptedAt': now_iso(), 'secretReference': 'op://api-keys/OpenAI/api_key'}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        value = read_key()
        output = os.open(cache, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(output, 'w') as stream:
                stream.write(value + '\n')
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            cache.unlink(missing_ok=True)
            raise
        return value
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def runner_environment(environment, state_root=None, classification=False):
    if state_root and credential_delay(state_root):
        raise CredentialRateLimited('1Password credential cooldown is active; no read attempted')
    clean = {key: value for key, value in environment.items() if key in ALLOWED}
    credential_environment = dict(clean)
    token = environment.get("OP_SERVICE_ACCOUNT_TOKEN")
    if token:
        credential_environment["OP_SERVICE_ACCOUNT_TOKEN"] = token
    references = {'OPENAI_API_KEY': 'op://api-keys/OpenAI/api_key'} if classification else REFERENCES
    def read_key(key, reference):
        if not token:
            raise RuntimeError("1Password service account token is unavailable")
        # Reuse the host's proven launchd/TCC isolation and process-group deadline.
        command = 'source "$1"; OP_CALL_TIMEOUT_SECONDS=20; trap op_cli_cleanup_isolated_home EXIT; op_with_deadline read "$2"'
        result = subprocess.run(["/bin/bash", "-c", command, "tutor-credential", str(OP_HELPER), reference],
                                env=credential_environment, capture_output=True, text=True, timeout=50)
        if result.returncode or not result.stdout.strip():
            if 'too many requests' in result.stderr.lower() or 'rate-limit' in result.stderr.lower():
                current = classification and state_root and Path(state_root).name == CURRENT_CLASSIFICATION_STAGE
                if state_root:
                    retry_at = (json.loads((Path(state_root) / 'job.json').read_text())['deadlineUnix']
                                if current else time.time() + 1800)
                    atomic_json(Path(state_root) / 'credential-wait.json', {'observedAt': now_iso(),
                        'nextAttemptUnix': retry_at, 'reason': '1Password rate limit',
                        'credentialName': key, 'paidRequestSubmitted': False})
                raise CredentialRateLimited('1Password rate-limited credential read; no further attempts in this window' if current else '1Password rate-limited credential reads; wait thirty minutes')
            raise RuntimeError("1Password credential read failed: " + key)
        return result.stdout.strip()
    for key, reference in references.items():
        if classification and state_root and Path(state_root).name == CURRENT_CLASSIFICATION_STAGE:
            clean[key] = cached_classification_key(Path(state_root), lambda: read_key(key, reference))
        else:
            clean[key] = read_key(key, reference)
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
