"""Explicit per-lesson corrections, outside the immutable published source graph.

No discovery: only records pinned in the authoritative ledger are effective.
Unregistered rows and model contexts are returned without changing their bytes.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

ALLOWED = frozenset({"english.grammar.distinguish-contraction-possession",
                     "english.grammar.complete-finite-clause",
                     "english.grammar.select-pronoun-form",
                     "english.language.expand-common-abbreviation",
                     "english.punctuation.punctuate-list",
                     "maths.fdp.convert-decimal-percentage",
                     "nvr.transform.reflect-figure",
                     "english.grammar.distinguish-phrase-clause",
                     "maths.fdp.recognise-equivalent-fractions",
                     "english.punctuation.punctuate-direct-speech",
                     "english.reading.sequence-events",
                     "maths.fdp.convert-fraction-percentage"})
ASPECTS = ("tall", "wide")
CONTRACT = "teaching-source-override/v1"
INPUT_MANIFEST = "data/teaching-social-inputs.json"
PUBLISHED_MANIFEST = "../../web/content/skill-videos/published.json"


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def checked_path(root, value, prefix="out/teaching-repairs/"):
    if not isinstance(value, str) or not value.startswith(prefix) or "\\" in value:
        raise ValueError("Correction path must be portable and task-owned")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("Unsafe correction path")
    target = root / path
    if not target.resolve().is_relative_to((root / prefix).resolve()):
        raise ValueError("Correction path escapes its task directory")
    # Reject aliases even when they happen to resolve within the same directory.
    if any(parent.is_symlink() for parent in [target, *target.parents] if parent != root.parent):
        raise ValueError("Symlinked correction paths are forbidden")
    return target


def checksum(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ValueError("Invalid SHA256 in correction record")
    return value


def current_bytes(path, expected, expected_bytes=None):
    before = path.stat()
    if digest(path) != checksum(expected):
        raise ValueError("Correction evidence hash changed: " + str(path))
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) \
            or (expected_bytes is not None and after.st_size != expected_bytes):
        raise ValueError("Correction evidence changed while hashing or has wrong size")


def load(root, registrations=None, hash_media=False):
    """Validate pinned text/provenance; media bytes are read only when requested."""
    if not registrations:
        return {}
    root = root.resolve()
    inputs_path, published_path = root / INPUT_MANIFEST, root / PUBLISHED_MANIFEST
    inputs = {r["skillKey"]: r for r in read(inputs_path)["videos"]}
    published = {r["skillKey"]: r for r in read(published_path)["videos"]}
    result = {}
    for skill, registration in registrations.items():
        if skill not in ALLOWED:
            raise ValueError("Unapproved teaching correction target: " + skill)
        path = checked_path(root, registration["path"])
        current_bytes(path, registration["sha256"])
        record = read(path)
        if record.get("contract") != CONTRACT or record.get("skillKey") != skill:
            raise ValueError("Correction record identity differs")
        revision = record.get("repairId", "")
        if not re.fullmatch("[a-z0-9][a-z0-9-]{0,79}", revision):
            raise ValueError("Invalid correction repairId")
        prefix = f"out/teaching-repairs/{skill}/{revision}/"
        checked_path(root, registration["path"], prefix)
        base, source, original = record["base"], inputs[skill], published[skill]
        if base.get("inputManifestSha256") != digest(inputs_path) \
                or base.get("publishedManifestSha256") != digest(published_path) \
                or base.get("inputRowSha256") != digest_json(source) \
                or base.get("publishedRowSha256") != digest_json(original):
            raise ValueError("Correction parent manifest or row changed")
        if record.get("ctaId") != source["ctaId"]:
            raise ValueError("Correction must retain the approved CTA assignment")
        assignments = read(root / "data/teaching-cta-assignments.json")
        if assignments.get(skill) != record["ctaId"]:
            raise ValueError("Correction CTA differs from the frozen assignment registry")
        approval = record.get("approval", {})
        if approval.get("scope") != "corrected-social-source-only" or any(
                not isinstance(approval.get(k), str) or not approval[k].strip()
                for k in ("reviewer", "approvedAt", "reason")):
            raise ValueError("Explicit correction authorisation is required")
        transcript = record.get("correctedTranscript")
        if not isinstance(transcript, list) or not transcript or any(
                not isinstance(t, str) or not t.strip() for t in transcript):
            raise ValueError("Complete approved corrected transcript is required")
        if set(record.get("media", {})) != set(ASPECTS) or set(record.get("outputPaths", {})) != set(ASPECTS):
            raise ValueError("Both corrected aspects are required")
        for aspect in ASPECTS:
            media = record["media"][aspect]
            original_sha = source["media"][aspect]["sha256"]
            if base.get("sources", {}).get(aspect) != original_sha or original["media"][aspect]["sha256"] != original_sha:
                raise ValueError("Correction original source identity differs")
            expected_dims = (1080, 1920) if aspect == "tall" else (1920, 1080)
            if (media.get("width"), media.get("height")) != expected_dims \
                    or type(media.get("bytes")) is not int or media["bytes"] <= 0:
                raise ValueError("Invalid corrected source metadata")
            checksum(media["sha256"])
            if media["sha256"] == original_sha:
                raise ValueError("Correction must identify newly corrected source bytes")
            source_path = checked_path(root, media["path"], prefix + "sources/")
            checked_path(root, record["outputPaths"][aspect], prefix + "blends/")
            if media["path"] != prefix + f"sources/{aspect}.mp4" \
                    or record["outputPaths"][aspect] != prefix + f"blends/{aspect}-cta.mp4":
                raise ValueError("Correction source/output paths do not match aspect")
            if hash_media:
                current_bytes(source_path, media["sha256"], media["bytes"])
                current_bytes(root / f"inputs/teaching/{skill}/{aspect}.mp4", original_sha,
                              source["media"][aspect]["bytes"])
        provenance = record.get("provenance")
        if not isinstance(provenance, list) or not provenance:
            raise ValueError("Exact repair provenance is required")
        for evidence in provenance:
            evidence_path = checked_path(root, evidence["path"], prefix)
            checksum(evidence["sha256"])
            if hash_media:
                current_bytes(evidence_path, evidence["sha256"])
        result[skill] = {"path": registration["path"], "sha256": registration["sha256"], "record": record}
    return result


def effective_source(row, override):
    result = copy.deepcopy(row)
    record = override["record"]
    for aspect in ASPECTS:
        result["media"][aspect] = {**record["media"][aspect],
            "originalSha256": record["base"]["sources"][aspect],
            "sourceOverrideSha256": override["sha256"],
            "derivativeAppRelativePath": record["outputPaths"][aspect]}
    result["sourceOverride"] = {"path": override["path"], "sha256": override["sha256"]}
    return result


def ledger_row(row, override, input_row):
    result = copy.deepcopy(row)
    source = effective_source(input_row, override)
    result["source_media"] = source["media"]
    result["source_override"] = override
    return result


def review_items(items, overrides, implementation):
    if not overrides:
        return items
    result = dict(items)
    for skill, override in overrides.items():
        identity = "teaching:" + skill
        item = copy.deepcopy(items[identity])
        context = item["context"]
        context["source_row"] = effective_source(context["source_row"], override)
        context["approved_body_script"] = "\n\n".join(override["record"]["correctedTranscript"])
        context["source_correction"] = {**override, "implementation": implementation,
            "scope": "Only the authorised social source is corrected. The published original is retained unchanged."}
        item["outputs"] = dict(override["record"]["outputPaths"])
        result[identity] = item
    return result


def implementation(root):
    return {name: digest(root / "scripts" / name)
            for name in ("teaching-source-overrides.py", "review-corrected-teaching.py")}
