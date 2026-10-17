#!/usr/bin/env python3
"""Studio-only blend/verification for explicitly registered corrected sources.

The ordinary blend encoder and verifier stay unchanged. No quality is approved.
"""
import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import uuid


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ledger = module("corrected_blend_ledger", "review-ledger.py")
blend = module("corrected_blend_base", "blend-teaching.py")
verify = module("corrected_verify_base", "verify-teaching-blend.py")
overrides = ledger.overrides
CONTRACT = "teaching-corrected-blend/v1"


@contextlib.contextmanager
def host_render_lease(directory=None):
    """Same directory/token contract as acquireRenderLock in render-batch-support.ts."""
    directory = directory or Path(tempfile.gettempdir()) / "smart-cub-founder-render.lock"
    try:
        directory.mkdir()
    except FileExistsError as exc:
        raise ValueError("Another founder render owns " + str(directory) + "; inspect its owner before cleanup") from exc
    token = uuid.uuid4().hex
    try:
        blend.atomic_json(directory / "owner.json", {"token": token, "pid": os.getpid(),
            "host": socket.gethostname(), "cwd": str(Path.cwd()), "startedAt": ledger.now()})
        yield
    finally:
        owner = directory / "owner.json"
        if owner.is_file() and ledger.read(owner).get("token") == token:
            shutil.rmtree(directory)


def resolve(root, authority, skill, aspect, hash_media=True):
    value = ledger.read(authority)
    resolved = overrides.load(root, value.get("teaching_source_overrides"), hash_media)
    if skill not in resolved:
        raise ValueError("No explicitly registered source correction")
    override = resolved[skill]
    row = next(r for r in ledger.read(root / overrides.INPUT_MANIFEST)["videos"] if r["skillKey"] == skill)
    effective = overrides.effective_source(row, override)
    record = override["record"]
    return value, override, effective, root / record["media"][aspect]["path"], root / record["outputPaths"][aspect]


def approved_cta(root, value, identity, aspect):
    path = root / f"out/{identity}-{aspect}.mp4"
    variant = value["items"][identity]["aspects"][aspect]
    if variant.get("state") != "passed" or (variant.get("review") or {}).get("output_sha256") != overrides.digest(path):
        raise ValueError("Current exact appended CTA is not formally passed")
    return path


def dependencies(root, override, effective, aspect, cta, encoder, version):
    return {"source": effective["media"][aspect], "ctaId": effective["ctaId"],
        "ctaSha256": overrides.digest(cta), "aspect": aspect, "encoder": encoder,
        "fps": 30, "sampleRate": 48000, "scriptSha256": overrides.digest(root / "scripts/blend-corrected-teaching.py"),
        "baseBlendScriptSha256": overrides.digest(root / "scripts/blend-teaching.py"),
        "verifierSha256": overrides.digest(root / "scripts/verify-teaching-blend.py"),
        "resolverSha256": overrides.digest(root / "scripts/teaching-source-overrides.py"),
        "sourceOverrideSha256": override["sha256"],
        "originalSourceSha256": override["record"]["base"]["sources"][aspect],
        "manifestSha256": overrides.digest(root / overrides.INPUT_MANIFEST), "ffmpegVersion": version}


def validate_receipt(root, override, effective, aspect, source, cta, output, receipt):
    dep = receipt.get("dependencies", {})
    expected = dependencies(root, override, effective, aspect, cta, dep.get("encoder"), dep.get("ffmpegVersion"))
    if receipt.get("contract") != CONTRACT or receipt.get("status") != "rendered-needs-review" \
            or receipt.get("skillKey") != effective["skillKey"] or dep != expected \
            or receipt.get("dependencyHash") != ledger.digest_json(dep) \
            or dep.get("encoder") not in {"videotoolbox", "software"} or not dep.get("ffmpegVersion"):
        raise ValueError("Corrected blend dependency receipt is stale")
    for field, path in (("sourcePath", source), ("ctaPath", cta)):
        if Path(receipt.get(field, "")).resolve() != path.resolve():
            raise ValueError("Corrected blend receipt path differs")
    original = root / f"inputs/teaching/{effective['skillKey']}/{aspect}.mp4"
    if Path(receipt.get("originalSourcePath", "")).resolve() != original.resolve():
        raise ValueError("Corrected blend original source path differs")
    actual = ledger.file_snapshot(root, str(output.relative_to(root)), True)
    recorded = receipt.get("output", {})
    if Path(recorded.get("path", "")).resolve() != output.resolve() \
            or recorded.get("sha256") != actual["sha256"] or recorded.get("bytes") != actual["bytes"]:
        raise ValueError("Corrected blend output bytes differ")
    return dep


def render(root, authority, skill, aspect, encoder):
    ledger.require_studio()
    value, override, effective, source, output = resolve(root, authority, skill, aspect)
    cta = approved_cta(root, value, effective["ctaId"], aspect)
    version = subprocess.run(["ffmpeg", "-version"], text=True, capture_output=True, check=True).stdout.splitlines()[0]
    dep = dependencies(root, override, effective, aspect, cta, encoder, version)
    receipt_path = output.with_suffix(".receipt.json")
    if output.exists() or receipt_path.exists():
        retained = ledger.read(receipt_path)
        validate_receipt(root, override, effective, aspect, source, cta, output, retained)
        if retained["dependencies"] != dep:
            raise ValueError("Immutable corrected output has different requested encoder dependencies")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name("." + output.stem + "." + uuid.uuid4().hex + ".partial.mp4")
    try:
        infos = [blend.media_info(blend.probe(path), aspect) for path in (source, cta)]
        reservation = blend.output_reservation(sum(info["durationSeconds"] for info in infos))
        blend.require_disk_space(shutil.disk_usage(output.parent).free, reservation)
        subprocess.run(blend.ffmpeg_command(source, cta, temporary, infos, encoder,
                       output_limit=reservation - 16 * 1024**2), check=True)
        # Re-read the registered authority and every dependency before publication.
        current_value, current_override, current_row, _, _ = resolve(root, authority, skill, aspect)
        approved_cta(root, current_value, effective["ctaId"], aspect)
        if current_override != override or dependencies(root, current_override, current_row, aspect, cta, encoder, version) != dep:
            raise ValueError("Correction dependencies changed during encoding")
        receipt = {"contract": CONTRACT, "schemaVersion": 1, "status": "rendered-needs-review",
            "host": socket.gethostname(), "renderedAt": ledger.now(), "skillKey": skill,
            "dependencies": dep, "dependencyHash": ledger.digest_json(dep), "inputs": infos,
            "sourcePath": str(source), "ctaPath": str(cta),
            "originalSourcePath": str(root / f"inputs/teaching/{skill}/{aspect}.mp4"),
            "output": {"path": str(output), "sha256": overrides.digest(temporary), "bytes": temporary.stat().st_size},
            "qualityApproved": False}
        os.replace(temporary, output)
        blend.atomic_json(receipt_path, receipt)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def verify_output(root, authority, skill, aspect):
    ledger.require_studio()
    value, override, effective, source, output = resolve(root, authority, skill, aspect)
    cta = approved_cta(root, value, effective["ctaId"], aspect)
    receipt = ledger.read(output.with_suffix(".receipt.json"))
    validate_receipt(root, override, effective, aspect, source, cta, output, receipt)
    source_meta, cta_meta = verify.probe(source), verify.probe(cta)
    first, last = verify.segment(source_meta, aspect), verify.segment(cta_meta, aspect)
    source_samples, cta_samples = verify.decoded_samples(source), verify.decoded_samples(cta)
    source_capacity, cta_capacity = first["frames"] * 1600, last["frames"] * 1600
    if source_samples > source_capacity or cta_samples > cta_capacity:
        raise ValueError("Corrected source or CTA original audio would be truncated")
    metrics = verify.check_output(verify.probe(output, count=True), aspect, first["frames"] + last["frames"],
        verify.decoded_samples(output), source_capacity + cta_samples, verify.mp4_audio_timing(output))
    plan = verify.boundary_plan(first, last, int(verify.streams(source_meta, aspect)[0]["nb_frames"]),
                               int(verify.streams(cta_meta, aspect)[0]["nb_frames"]))
    folder = output.parent / (aspect + "-boundary")
    artifacts_path = output.with_suffix(".boundary-artifacts.json")
    if artifacts_path.exists():
        artifacts = ledger.read(artifacts_path)
        for item in artifacts:
            overrides.current_bytes(root / item["path"], item["sha256"])
    else:
        artifacts = verify.evidence(plan, {"source": source, "cta": cta, "output": output}, folder)
        artifacts = [{**item, "path": str(Path(item["path"]).relative_to(root))} for item in artifacts]
        verify.save_new(artifacts_path, artifacts)
    record = {"contract": CONTRACT, "status": "technical-pass-quality-review-pending", "qualityApproved": False,
        "sourceSha256": effective["media"][aspect]["sha256"], "ctaSha256": overrides.digest(cta),
        "outputSha256": overrides.digest(output), "sourceOverrideSha256": override["sha256"],
        "originalSourceSha256": override["record"]["base"]["sources"][aspect],
        "manifestSha256": overrides.digest(root / overrides.INPUT_MANIFEST),
        "correctionRendererSha256": overrides.digest(Path(__file__)),
        "verifierSha256": overrides.digest(root / "scripts/verify-teaching-blend.py"),
        "decodedSourceAudioSamples": source_samples, "decodedCtaAudioSamples": cta_samples,
        "boundaryPlan": plan, "boundaryArtifacts": artifacts, **metrics}
    validate_receipt(root, override, effective, aspect, source, cta, output, receipt)
    target = output.with_suffix(".mechanical-verification.json")
    if target.exists():
        if ledger.read(target) != record:
            raise ValueError("Immutable correction verification differs")
    else:
        verify.save_new(target, record)
    return record


def preflight_output(root, authority, skill, aspect):
    ledger.require_studio()
    value, override, effective, source, output = resolve(root, authority, skill, aspect)
    cta = approved_cta(root, value, effective["ctaId"], aspect)
    validate_receipt(root, override, effective, aspect, source, cta, output,
                     ledger.read(output.with_suffix(".receipt.json")))
    proof = ledger.read(output.with_suffix(".mechanical-verification.json"))
    if proof.get("status") != "technical-pass-quality-review-pending" or proof.get("qualityApproved") is not False \
            or proof.get("outputSha256") != overrides.digest(output) \
            or proof.get("sourceSha256") != effective["media"][aspect]["sha256"] \
            or proof.get("ctaSha256") != overrides.digest(cta) \
            or proof.get("sourceOverrideSha256") != override["sha256"] \
            or proof.get("originalSourceSha256") != override["record"]["base"]["sources"][aspect] \
            or proof.get("manifestSha256") != overrides.digest(root / overrides.INPUT_MANIFEST) \
            or proof.get("correctionRendererSha256") != overrides.digest(root / "scripts/blend-corrected-teaching.py") \
            or proof.get("verifierSha256") != overrides.digest(root / "scripts/verify-teaching-blend.py"):
        raise ValueError("Corrected mechanical evidence is stale")
    for artifact in proof.get("boundaryArtifacts", []):
        overrides.current_bytes(root / ledger.portable(root, artifact["path"]), artifact["sha256"])
    if len(proof.get("boundaryArtifacts", [])) != 12:
        raise ValueError("Complete corrected-source/join/end evidence is required")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("plan", "render", "verify"), default="plan")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--id", choices=sorted(overrides.ALLOWED), required=True)
    parser.add_argument("--aspect", choices=("wide", "tall", "both"), default="both")
    parser.add_argument("--encoder", choices=("videotoolbox", "software"), default="videotoolbox")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    root, authority = args.root.resolve(), args.ledger.resolve()
    if args.mode == "plan":
        _, override, _, _, _ = resolve(root, authority, args.id, "wide", False)
        print(json.dumps({"state": "plan-only", "override_sha256": override["sha256"],
                          "outputs": override["record"]["outputPaths"]}, indent=2))
        return
    if not args.execute:
        parser.error("Media work needs --execute on Studio")
    ledger.require_studio()
    os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
    lock = root / "out/teaching-repairs/.blend.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"pid": os.getpid(), "host": socket.gethostname()}, handle)
        with host_render_lease():
            for aspect in (ledger.ASPECTS if args.aspect == "both" else (args.aspect,)):
                if args.mode == "render":
                    render(root, authority, args.id, aspect, args.encoder)
                result = verify_output(root, authority, args.id, aspect)
                print(json.dumps(result), flush=True)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
