#!/usr/bin/env python3
"""Regenerate explicitly selected approved voices, on Studio, with a shared budget guard.

Plan without media processing or API calls:
  python3 scripts/voice-regenerate.py plan --ids cta-sc-07 cta-sc-09 cta-sc-15

After reviewing copy, run on Studio with the ONE authoritative ledger:
  python3 scripts/voice-regenerate.py run --ids cta-sc-07 cta-sc-09 cta-sc-15 \
    --ledger ~/render-tmp/smart-cub-refresh-20261016/ledger/media-review-ledger.json

For selected marketing repairs, add --file data/scripts.json and explicit marketing IDs.
The original tts.ts and pace.py are copied unchanged into an isolated per-ID workspace.
Only the selected row is supplied. James's existing PVC and the row's original preset
are retained. Pacing is exactly 1.075, without processing or installing avatar clips.

A Node fetch guard reserves US$2 before its single bounded ElevenLabs request (at most
2000 spoken characters), saves intent before POST, headers before reading the body, and
the full response with alignment. Unknown dollar costs remain reserved. character-cost
is usage metadata, not a dollar invoice. Reconcile through review-ledger.py settle only
when billing is known. No automatic paid retries. --resume-local can replay an intact
known response without another provider request; it cannot retry an uncertain request.

Pricing/receipt references checked 2026-10-16:
https://elevenlabs.io/docs/api-reference/introduction
https://elevenlabs.io/pricing/api
No dollar tariff is inferred for the existing eleven_v4 preset or subscription account.
The guard first reads provider model factors, own-PVC metadata and remaining included
account credits, and refuses a request not covered by that observed included quota.
Do not run another task-owned voice batch concurrently against the same credit pool.
"""
import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import uuid

SPEC = importlib.util.spec_from_file_location("voice_ledger", Path(__file__).with_name("review-ledger.py"))
ledger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger)
ROOT = Path(__file__).resolve().parents[1]
VOICE = "1CdujsFVRQeoIxTwAYmB"
PACE = 1.075


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another process owns this voice job or shared-budget voice batch") from exc
        yield


def plans(root, registry, ids):
    if not ids:
        raise ValueError("An explicit non-empty --ids selection is required")
    if registry not in {"data/ctas.json", "data/scripts.json"}:
        raise ValueError("Only the approved CTA or marketing script registry may be used")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate selected IDs are not allowed")
    rows = {row["id"]: row for row in ledger.read(root / registry)}
    unknown = set(ids) - set(rows)
    if unknown:
        raise ValueError("Unknown selected IDs: " + ", ".join(sorted(unknown)))
    presets = ledger.read(root / "data/voice-presets.json")
    hashes = {name: ledger.sha256(root / name) for name in ("scripts/tts.ts", "scripts/pace.py", "data/voice-presets.json")}
    result = []
    for identity in ids:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", identity):
            raise ValueError("Unsafe selected ID")
        row = rows[identity]
        preset_name = row.get("preset", "baseline")
        preset = presets.get(preset_name)
        if not preset or not isinstance(row.get("script"), str) or not row["script"].strip():
            raise ValueError("Missing approved script or original voice preset")
        inputs = {"id": identity, "script": row["script"], "voice": VOICE, "preset_name": preset_name,
                  "preset": preset, "pace": PACE, "tts_sha256": hashes["scripts/tts.ts"], "pace_sha256": hashes["scripts/pace.py"]}
        result.append({"id": identity, "source_registry": registry, "source_registry_sha256": ledger.sha256(root / registry),
                       "source_row": row, "voice_inputs": inputs, "version_key": ledger.digest_json(inputs),
                       "expected_voice": VOICE, "expected_preset": preset_name, "expected_model": preset["model"],
                       "expected_settings": preset["voice_settings"], "source_hashes": hashes,
                       "pace": PACE, "maximum_cents": 200})
    return result


def require_inactive_avatar(root, identity):
    path = root / "data/avatars.json"
    if path.is_file() and ledger.read(path).get(identity):
        raise ValueError(f"{identity}: active avatar must be retired or disabled before changing its voice; old lip sync would be stale")


def prepare_workspace(root, plan, workspace):
    for directory in ("scripts", "data", "public/audio", "data/words"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    for name in ("tts.ts", "pace.py"):
        shutil.copyfile(root / "scripts" / name, workspace / "scripts" / name)
    shutil.copyfile(root / "data/voice-presets.json", workspace / "data/voice-presets.json")
    atomic_json(workspace / "package.json", {"type": "module", "private": True})
    atomic_json(workspace / "data/selected.json", [plan["source_row"]])
    # Do not stage an avatar, even if an unused historical clip exists in the real project.


def output_paths(identity):
    return (f"public/audio/{identity}.mp3", f"data/words/{identity}.json",
            f"public/audio/raw/{identity}.mp3", f"data/words/raw/{identity}.json")


def verify_workspace(workspace, plan):
    for script, field in (("tts.ts", "tts_sha256"), ("pace.py", "pace_sha256")):
        if ledger.sha256(workspace / "scripts" / script) != plan["voice_inputs"][field]:
            raise ValueError("Isolated legacy script changed; refusing to execute it")
    selected = ledger.read(workspace / "data/selected.json")
    if len(selected) != 1 or any(selected[0].get(key) != plan["source_row"].get(key) for key in ("id", "script", "preset")):
        raise ValueError("Isolated selected script differs from the approved voice input")
    presets = ledger.read(workspace / "data/voice-presets.json")
    if presets.get(plan["expected_preset"]) != plan["voice_inputs"]["preset"]:
        raise ValueError("Isolated preset changed; refusing a provider request")


def validate_outputs(workspace, plan):
    for relative in output_paths(plan["id"]):
        if not (workspace / relative).is_file() or (workspace / relative).stat().st_size == 0:
            raise ValueError("Selected generated output is missing or empty")
    words = ledger.read(workspace / f"data/words/{plan['id']}.json")
    raw = ledger.read(workspace / f"data/words/raw/{plan['id']}.json")
    for key, expected in (("id", plan["id"]), ("voice", VOICE), ("model", plan["expected_model"]), ("preset", plan["expected_preset"])):
        if words.get(key) != expected or raw.get(key) != expected:
            raise ValueError("Voice or preset changed unexpectedly")
    if words.get("pace") != PACE or "pace" in raw:
        raise ValueError("Expected one 7.5% audio pacing pass with an untouched raw take")
    duration = words.get("durationMs")
    if not isinstance(duration, int) or duration <= 0 or not words.get("words"):
        raise ValueError("Invalid generated word timings")
    if [word["t"] for word in words["words"]] != [word["t"] for word in raw["words"]]:
        raise ValueError("Pacing changed display words")
    previous = -1
    for word, raw_word in zip(words["words"], raw["words"]):
        start, end = word.get("s"), word.get("e")
        if not isinstance(start, int) or not isinstance(end, int) or not previous <= start <= end <= duration + 100:
            raise ValueError("Generated word timings are invalid")
        if start != round(raw_word["s"] * PACE) or end != round(raw_word["e"] * PACE):
            raise ValueError("Word timings were not paced exactly once")
        previous = start


def replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def file_hash(path):
    return ledger.sha256(path) if path.is_file() else None


def rollback_install(root, job_directory):
    journal_path = job_directory / "install.json"
    journal = ledger.read(journal_path)
    for item in reversed(journal["files"]):
        target = root / ledger.portable(root, item["path"])
        current = file_hash(target)
        if current == item["before_sha256"]:
            continue
        if current != item["new_sha256"]:
            raise ValueError("A concurrent output change was preserved; inspect the install journal before recovery")
        if item["before_sha256"]:
            backup = job_directory / "before" / item["path"]
            if file_hash(backup) != item["before_sha256"]:
                raise ValueError("Backup integrity failed; refusing to overwrite existing work")
            replace_file(backup, target)
        else:
            target.unlink()
    journal.update({"state": "rolled_back", "rolled_back_at": ledger.now()})
    atomic_json(journal_path, journal)


def install_outputs(root, workspace, job_directory, identity):
    journal_path = job_directory / "install.json"
    if journal_path.exists() and ledger.read(journal_path)["state"] == "installing":
        raise ValueError("Previous install was interrupted; use --recover-install before any regeneration")
    entries = []
    for relative in output_paths(identity):
        target = root / relative
        before = file_hash(target)
        if before:
            backup = job_directory / "before" / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, backup)
            with backup.open("rb") as preserved:
                os.fsync(preserved.fileno())
            directory = os.open(backup.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if file_hash(backup) != before or file_hash(target) != before:
                raise ValueError("An output changed during backup; existing work was preserved")
        entries.append({"path": relative, "before_sha256": before, "new_sha256": ledger.sha256(workspace / relative)})
    journal = {"state": "installing", "files": entries, "started_at": ledger.now()}
    atomic_json(journal_path, journal)
    try:
        for entry in entries:
            target = root / entry["path"]
            if file_hash(target) != entry["before_sha256"]:
                raise ValueError("An output changed concurrently; refusing to overwrite it")
            replace_file(workspace / entry["path"], target)
        journal.update({"state": "installed", "installed_at": ledger.now()})
        atomic_json(journal_path, journal)
    except BaseException:
        rollback_install(root, job_directory)
        raise
    return [{"path": item["path"], "sha256": item["new_sha256"]} for item in entries]


def current_outputs(root, job):
    return bool(job.get("state") == "complete" and job.get("outputs") and all(
        file_hash(root / entry["path"]) == entry["sha256"] for entry in job["outputs"]))


def child_environment(job_path, ledger_path, resume_local):
    # The child needs only the existing service account and ordinary process settings.
    # Do not forward unrelated provider keys or arbitrary NODE_OPTIONS preloads.
    allowed = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "OP_SERVICE_ACCOUNT_TOKEN")
    result = {key: os.environ[key] for key in allowed if key in os.environ}
    result.update({"SCL_VOICE_JOB_PATH": str(job_path), "SCL_VOICE_LEDGER_PATH": str(ledger_path),
                   "SCL_VOICE_LOCAL_REPLAY": "1" if resume_local else "0"})
    return result


def require_runtime_tools():
    missing = [tool for tool in ("node", "ffprobe", "ffmpeg", "sox") if not shutil.which(tool)]
    if missing:
        raise ValueError("Missing required executable(s): " + ", ".join(missing) + "; install them on Studio before any voice request")


def run_stage(stage, command, *, root, job_path, env=None):
    """Keep local diagnostics while never persisting credential-bearing TTS output."""
    if stage not in {"tts", "pacing"}:
        raise ValueError("Unknown local voice stage")
    result, failure = None, None
    try:
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    except OSError as exc:
        # Exception messages can include child arguments or environment details.
        failure = {"type": type(exc).__name__, "errno": exc.errno}
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    record = {"stage": stage, "recorded_at": ledger.now(),
              "returncode": result.returncode if result is not None else None,
              "launch_error": failure, "stdout_characters": len(stdout), "stderr_characters": len(stderr)}
    if stage == "tts":
        record.update({"stdout_policy": "omitted_provider_or_credential_bearing_output",
                       "stderr_policy": "omitted_provider_or_credential_bearing_output",
                       "diagnostic": "Use saved guard state, provider IDs and response integrity evidence; raw TTS output is not logged"})
    else:
        record.update({"stdout": stdout, "stderr": stderr, "stderr_policy": "local_media_stage_only"})
    log_path = job_path.parent / "local-stage-logs" / f"{stage}-{uuid.uuid4().hex}.json"
    atomic_json(log_path, record)
    job = ledger.read(job_path)
    entry = {"stage": stage, "log_path": log_path.relative_to(job_path.parent).as_posix(),
             "returncode": record["returncode"], "recorded_at": record["recorded_at"]}
    job.setdefault("local_stages", []).append(entry)
    if failure or result.returncode:
        job["last_local_failure"] = {**entry, "type": failure["type"] if failure else "nonzero_exit"}
    atomic_json(job_path, job)
    if failure:
        raise ValueError(f"{stage}: local child could not launch; inspect the safe local stage log")
    return result


def run_one(root, plan, ledger_path, jobs_root, resume_local=False, recover_install=False):
    ledger.require_studio()
    identity = plan["id"]
    directory = jobs_root / identity / plan["version_key"][:24]
    job_path = directory / "job.json"
    workspace = directory / "work"
    with exclusive(jobs_root / identity / ".voice.lock"):
        for old_path in (jobs_root / identity).glob("*/job.json"):
            old = ledger.read(old_path)
            if old_path != job_path and old.get("reservation_id") and old.get("state") != "complete":
                raise ValueError("An earlier selected voice request is unresolved; reconcile it before a new paid request")
        old = ledger.read(job_path) if job_path.is_file() else None
        if recover_install:
            if not old:
                raise ValueError("No matching voice job to recover")
            rollback_install(root, directory)
            return {"id": identity, "state": "install_rolled_back", "job": str(job_path), "paid_requests": 0}
        if old and current_outputs(root, old):
            return {"id": identity, "state": "skipped_exact_voice_version", "job": str(job_path), "paid_requests": 0}
        # Check the entire local pipeline before the legacy TTS child reads a key
        # or its guard performs provider preflight and a potentially paid POST.
        require_runtime_tools()
        require_inactive_avatar(root, identity)
        if resume_local and (not old or not old.get("response_sha256") or file_hash(Path(old["response_path"])) != old["response_sha256"]):
            raise ValueError("Local-only recovery requires an intact known response; no new paid request")
        if old and old.get("reservation_id"):
            if not resume_local or not old.get("response_sha256") or file_hash(Path(old["response_path"])) != old["response_sha256"]:
                raise ValueError("Prior voice request is uncertain or unresolved; no automatic paid retry. Use --resume-local only with an intact saved response")
        if not old:
            prepare_workspace(root, plan, workspace)
            old = {**plan, "state": "prepared", "created_at": ledger.now(),
                   "response_path": str(directory / "provider-response.json"),
                   "machine": socket.gethostname(), "budget_state": "not_reserved",
                   "legacy_tts_unchanged": True, "avatar_processing": "excluded_from_isolated_workspace"}
            atomic_json(job_path, old)
        if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
            raise ValueError("Load OP_SERVICE_ACCOUNT_TOKEN from the existing shell profile first")
        verify_workspace(workspace, plan)
        command = ["node", "--import", "tsx", "--import", str(Path(__file__).with_name("voice-budget-guard.mjs")),
                   str(workspace / "scripts/tts.ts"), "--file", "data/selected.json", "--voice", VOICE,
                   "--preset", plan["expected_preset"], identity]
        process = run_stage("tts", command, root=root, job_path=job_path,
                            env=child_environment(job_path, ledger_path, resume_local))
        if process.returncode:
            raise ValueError(f"{identity}: selected TTS stage failed; inspect the durable job/receipt. No automatic paid retry")
        job = ledger.read(job_path)
        job.update({"state": "tts_complete", "tts_complete_at": ledger.now()})
        atomic_json(job_path, job)
        pace = run_stage("pacing", ["python3", "-I", str(workspace / "scripts/pace.py"), "--pace", str(PACE), identity],
                         root=workspace, job_path=job_path)
        if pace.returncode:
            raise ValueError(f"{identity}: local pacing failed; preserve the saved response and use --resume-local after repair")
        validate_outputs(workspace, plan)
        current = plans(root, plan["source_registry"], [identity])[0]
        if current["version_key"] != plan["version_key"]:
            raise ValueError("Approved voice inputs changed during generation; staged outputs retained, nothing installed")
        require_inactive_avatar(root, identity)
        outputs = install_outputs(root, workspace, directory, identity)
        job = ledger.read(job_path)
        job.update({"state": "complete", "completed_at": ledger.now(), "outputs": outputs,
                    "budget_state": "reserved_usd_cost_unconfirmed", "pace": PACE,
                    "audio_review": "pending", "render_review": "pending", "avatar_review": "not_generated"})
        atomic_json(job_path, job)
        return {"id": identity, "state": "complete_audio_review_pending", "job": str(job_path),
                "paid_requests": 0 if resume_local else 1, "held_reservation_cents": 200, "outputs": outputs}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("plan", "run"))
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--file", choices=("data/ctas.json", "data/scripts.json"), default="data/ctas.json")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--jobs", type=Path, default=Path("out/voice-jobs"))
    parser.add_argument("--resume-local", action="store_true", help="Replay an intact known response; never submit a paid retry")
    parser.add_argument("--recover-install", action="store_true", help="Roll back an interrupted installation from retained backups, no API call")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        chosen = plans(root, args.file, args.ids)
        if args.mode == "plan":
            print(json.dumps({"state": "plan_only_no_media_or_api_calls", "selected": [{key: plan[key] for key in
                ("id", "source_registry", "version_key", "expected_voice", "expected_preset", "expected_model", "pace", "maximum_cents")} for plan in chosen]}, indent=2))
            return
        ledger.require_studio()
        if args.ledger is None or not args.ledger.expanduser().is_file():
            raise ValueError("Run mode requires the existing authoritative --ledger")
        jobs = args.jobs if args.jobs.is_absolute() else root / args.jobs
        authority = args.ledger.expanduser().resolve()
        # Serialise batches by the authoritative ledger, including callers choosing
        # another jobs directory. Per-ID locks also protect direct local recovery.
        with exclusive(authority.with_name(authority.name + ".voice-batch.lock")):
            for plan in chosen:
                print(json.dumps(run_one(root, plan, authority, jobs.resolve(),
                                         args.resume_local, args.recover_install)), flush=True)
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.exit(1, f"voice-regenerate: {exc}\n")


if __name__ == "__main__":
    main()
