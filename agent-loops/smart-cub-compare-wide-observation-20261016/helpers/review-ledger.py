#!/usr/bin/env python3
"""Durable media review inventory. No rendering, downloads, or paid API calls.

Media-byte hashing and review records are restricted to the Mac Studio. Writes use
flock and atomic replacement; paid reservations share the same transaction lock.
"""
import argparse
import contextlib
import datetime
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import tempfile

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
ASPECTS = ("tall", "wide")
SOURCE_DIRS = ("src", "public")
SOURCE_DATA_FILES = ("scripts.json", "ctas.json", "cta-assignments.json", "avatars.json",
                     "teaching-cta-assignments.json")
_override_spec = importlib.util.spec_from_file_location("ledger_teaching_overrides", Path(__file__).with_name("teaching-source-overrides.py"))
overrides = importlib.util.module_from_spec(_override_spec)
_override_spec.loader.exec_module(overrides)
_root_spec = importlib.util.spec_from_file_location("ledger_production_roots", Path(__file__).with_name("production-roots.py"))
production = importlib.util.module_from_spec(_root_spec)
_root_spec.loader.exec_module(production)


def _ledger_api():
    from types import SimpleNamespace
    return SimpleNamespace(**globals())


def register_marketing_root(root, path, revision_root, cache_map, reviewer, reason, expected_render_fingerprint=None):
    require_studio()
    before = read(path)
    bound = production.build(before, root, revision_root, cache_map, reviewer, reason, _ledger_api(), expected_render_fingerprint)
    expected = digest_json({key: before.get(key) for key in ("source_snapshot", "asset_replacement", "production_roots")})
    with transaction(path) as value:
        if digest_json({key: value.get(key) for key in ("source_snapshot", "asset_replacement", "production_roots")}) != expected:
            raise ValueError("Production authority changed during registration")
        production.validate_binding(value, bound, _ledger_api())
        value["production_roots"] = {"marketing": bound}
        for item in value["items"].values():
            if item["kind"] == "marketing":
                for variant in item["aspects"].values():
                    if variant.get("review"):
                        reopen(variant, "Marketing production root explicitly changed; fresh exact-version judgement required")
                    variant["ready_for_review"] = False
    return value


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def is_studio():
    # Do not accept environment overrides as evidence of the actual host.
    name = re.sub(r"[^a-z0-9]", "", socket.gethostname().lower().split(".")[0])
    return name in {"jamessmacstudio", "jamesmacstudio"}


def require_studio():
    if not is_studio():
        raise ValueError("Media hashing and review records must run on jamess-mac-studio")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def portable(root, value):
    path = Path(value)
    if path.is_absolute():
        raise ValueError("Paths must be relative to the founder-videos project")
    resolved = (root / path).resolve()
    # Local evidence and media must stay within the project, including symlinks.
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Path escapes the founder-videos project")
    return path.as_posix()


def file_snapshot(root, relative, hash_bytes):
    relative = portable(root, relative)
    path = root / relative
    if not path.is_file():
        return {"path": relative, "exists": False, "sha256": None, "bytes": None}
    stat = path.stat()
    checksum = sha256(path) if hash_bytes else None
    after = path.stat()
    if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f"File changed while hashing: {relative}; wait for its writer to finish")
    return {"path": relative, "exists": True,
            "sha256": checksum, "bytes": stat.st_size}


def initial():
    return {
        "schema_version": 1,
        "created_at": now(),
        "updated_at": now(),
        "objective": "Review, repair and improve every marketing video, CTA and teaching social derivative",
        "priority": "Clean and replace the character asset library before media review and rendering",
        "policy": {
            "media_host": "jamess-mac-studio",
            "macbook_role": "Agent orchestration and code editing only",
            "lip_sync": "Selected CTA endings only; no marketing or teaching body lip sync",
            "public_teaching_sources": "Preserve existing public lesson videos; create separate CTA social derivatives",
            "review_scope": "Every complete output in both tall and wide aspects, picture and sound",
            "required_checks": ["full_video_playback", "layout_and_readability", "narration_and_captions",
                                "brand_and_claims", "cta_relevance_and_transition", "audio_and_export",
                                "selected_cta_lip_sync"],
            "pass_rule": "A human or agent must explicitly record exact-version evidence; inventory never grants a pass",
            "budget_rule": "Reserve the maximum cost before every paid request; retain uncertain reservations until reconciled",
        },
        "asset_replacement": {
            "state": "pending", "manifest": None, "manifest_sha256": None,
            "requested_originals": ["IMG_4257.HEIC", "IMG_4254.HEIC", "IMG_4253.HEIC", "IMG_4252.HEIC",
                                    "IMG_4247.heic", "IMG_4244.HEIC", "IMG_4243.HEIC"],
            "note": "Originals remain in Downloads; use a portable project manifest for Studio copies and derivatives",
        },
        "budget": {"currency": "USD", "cap_cents": 10000, "reservations": {}},
        "teaching_discovery": {"state": "pending", "expected_count": 144, "discovered_ids": [],
                               "source_page": "https://smartcublearning.com/11-plus/videos/",
                               "note": "Discover existing published social assets before assigning CTAs"},
        "source_snapshot": None,
        "items": {},
    }


@contextlib.contextmanager
def transaction(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        value = read(path) if path.exists() else initial()
        yield value
        value["updated_at"] = now()
        descriptor, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(value, output, indent=2, ensure_ascii=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def source_snapshot(root, hash_media, asset_manifest=None):
    paths = []
    for directory in SOURCE_DIRS:
        base = root / directory
        if base.exists():
            paths += [path for path in base.rglob("*") if path.is_file() and not path.name.startswith(".")]
    for name in SOURCE_DATA_FILES:
        if (root / "data" / name).is_file():
            paths.append(root / "data" / name)
    for directory in ("data/words", "data/durations"):
        if (root / directory).exists():
            paths += [path for path in (root / directory).rglob("*.json") if path.is_file()]
    for name in ("package.json", "package-lock.json", "tsconfig.json", "scripts/render.ts"):
        if (root / name).is_file():
            paths.append(root / name)
    if asset_manifest:
        paths.append(root / portable(root, asset_manifest))
    files = []
    for path in sorted(set(paths)):
        relative = path.relative_to(root).as_posix()
        # public/ holds media. Do not read these bytes on the orchestration host.
        files.append(file_snapshot(root, relative, hash_media or not relative.startswith("public/")))
    complete = bool(hash_media) and all(entry["sha256"] for entry in files)
    return {"captured_at": now(), "machine": socket.gethostname(), "complete": complete,
            "fingerprint": digest_json(files), "files": files,
            "scope": "Conservative shared dependency snapshot: src, public, input registries, words, package lock and renderer"}


def rows(root, source_overrides=None):
    scripts = read(root / "data/scripts.json")
    ctas = read(root / "data/ctas.json")
    assignments = read(root / "data/cta-assignments.json")
    teaching_assignment_path = root / "data/teaching-cta-assignments.json"
    teaching_assignments = read(teaching_assignment_path) if teaching_assignment_path.exists() else {}
    manifest = root / "../../web/content/skill-videos/published.json"
    teaching = read(manifest).get("videos", []) if manifest.is_file() else []
    all_rows = []
    for kind, items in (("marketing", scripts), ("cta", ctas)):
        for row in items:
            identity = row["id"]
            all_rows.append((identity, {
                "kind": kind, "title": row.get("title", row.get("head", identity)),
                "audience": row.get("audience"), "source_row_sha256": digest_json(row),
                "source_registry": "data/scripts.json" if kind == "marketing" else "data/ctas.json",
                "cta_id": assignments.get(identity) if kind == "marketing" else identity,
                "lip_sync_selected": bool(row.get("circle")) if kind == "cta" else False,
                "source_media": None,
            }))
    for row in teaching:
        identity = "teaching:" + row["skillKey"]
        item = {
            "kind": "teaching_social_derivative", "title": row["title"], "audience": "parents",
            "skill_key": row["skillKey"], "source_row_sha256": digest_json(row),
            "source_registry": "../../web/content/skill-videos/published.json",
            "cta_id": teaching_assignments.get(row["skillKey"]), "lip_sync_selected": False,
            "source_media": {aspect: row["media"][aspect] for aspect in ASPECTS},
            "original_approval": row.get("approval"),
        }
        if row["skillKey"] in (source_overrides or {}):
            input_row = next(r for r in read(root / "data/teaching-social-inputs.json")["videos"]
                             if r["skillKey"] == row["skillKey"])
            item = overrides.ledger_row(item, source_overrides[row["skillKey"]], input_row)
        all_rows.append((identity, item))
    if len({identity for identity, _ in all_rows}) != len(all_rows):
        raise ValueError("Duplicate inventory IDs")
    cta_by_id = {row["id"]: row for row in ctas}
    for identity, row in all_rows:
        if row["cta_id"] is not None and row["cta_id"] not in cta_by_id:
            raise ValueError(f"Unknown CTA for {identity}: {row['cta_id']}")
        if row["kind"] != "cta" and row["cta_id"]:
            row["cta_source_row_sha256"] = digest_json(cta_by_id[row["cta_id"]])
    discovery = {"state": "manifest_discovered" if teaching else "pending", "expected_count": 144,
                 "discovered_ids": [row["skillKey"] for row in teaching],
                 "source_page": "https://smartcublearning.com/11-plus/videos/",
                 "manifest": "../../web/content/skill-videos/published.json" if teaching else None,
                 "manifest_sha256": sha256(manifest) if teaching else None,
                 "note": "Published source hashes are declared by the manifest; verify downloaded bytes on Studio. Original approvals do not approve CTA derivatives."}
    return all_rows, discovery


def aspect_input(row, aspect, root, hash_media, shared_fingerprint):
    clip = None
    expected = None
    if row["kind"] == "teaching_social_derivative":
        relative = (row["source_override"]["record"]["media"][aspect]["path"] if row.get("source_override")
                    else f"inputs/teaching/{row['skill_key']}/{aspect}.mp4")
        clip = file_snapshot(root, relative, hash_media)
        expected = row["source_media"][aspect]["sha256"]
    return {"shared_source_fingerprint": shared_fingerprint,
            "item_source_sha256": digest_json(row), "source_clip": clip,
            "source_clip_expected_sha256": expected}


def output_path(identity, row, aspect):
    if row["kind"] == "teaching_social_derivative":
        if row.get("source_override"):
            return row["source_override"]["record"]["outputPaths"][aspect]
        return f"out/teaching/{row['skill_key']}-{aspect}-cta.mp4"
    return f"out/{identity}-{aspect}.mp4"


def evidence_current(root, review):
    return all(file_snapshot(root, item["path"], True)["sha256"] == item["sha256"]
               for item in review["evidence"])


def reopen(variant, reason):
    if variant.get("review"):
        variant["history"].append({"review": variant["review"], "reopened_at": now(), "reason": reason})
    variant["review"] = None
    variant["state"] = "reopened"
    variant["reopen_reason"] = reason


def apply_inventory(value, root, hash_media, kinds=None):
    if hash_media:
        require_studio()
    family = production.kind_for_root(value, root)
    selected_kinds = set(kinds or (("marketing",) if family == "marketing" else
                         ("cta", "teaching_social_derivative") if family == "canonical" else production.KINDS))
    if not selected_kinds or selected_kinds - set(production.KINDS) or (family == "marketing" and selected_kinds != {"marketing"}) \
            or (family == "canonical" and "marketing" in selected_kinds):
        raise ValueError("Inventory kinds do not belong to this configured production root")
    if family:
        if not hash_media:
            raise ValueError("Bound family inventories require complete Studio hashes")
        snapshot, _ = production.validate(value, root, "marketing" if family == "marketing" else "cta", _ledger_api())
    else:
        snapshot = source_snapshot(root, hash_media, value["asset_replacement"]["manifest"])
    source_overrides = overrides.load(root, value.get("teaching_source_overrides"), hash_media) if family != "marketing" else {}
    inventory_rows, discovery = rows(root, source_overrides)
    inventory_rows = [(identity, row) for identity, row in inventory_rows if row["kind"] in selected_kinds]
    current_ids = {identity for identity, _ in inventory_rows}
    for identity, item in value["items"].items():
        if item["kind"] in selected_kinds and identity not in current_ids:
            item["in_current_inventory"] = False
            for variant in item["aspects"].values():
                reopen(variant, "Item removed from source registry; retained for audit")
    for identity, row in inventory_rows:
        item = value["items"].setdefault(identity, {"aspects": {}})
        item.update(row)
        item["in_current_inventory"] = True
        for aspect in ASPECTS:
            inputs = aspect_input(row, aspect, root, hash_media, snapshot["fingerprint"])
            output = file_snapshot(root, output_path(identity, row, aspect), hash_media)
            variant = item["aspects"].setdefault(aspect, {"state": "pending", "review": None, "history": []})
            review = variant["review"]
            if review and (not snapshot["complete"] or review["input_sha256"] != digest_json(inputs)
                           or review["output_sha256"] != output["sha256"]
                           or not evidence_current(root, review)):
                reopen(variant, "Sources, output or evidence changed, or complete media hashes are unavailable")
            variant["inputs"] = inputs
            variant["input_sha256"] = digest_json(inputs)
            variant["output"] = output
            variant["ready_for_review"] = bool(snapshot["complete"] and output["sha256"] and row["cta_id"]
                and (inputs["source_clip"] is None or inputs["source_clip"]["sha256"] == inputs["source_clip_expected_sha256"]))
    if family != "marketing":
        value["source_snapshot"] = snapshot
        value["teaching_discovery"] = discovery
    value["inventory_counts"] = {kind: sum(item["kind"] == kind and item.get("in_current_inventory", False)
                                         for item in value["items"].values()) for kind in production.KINDS}
    value["inventory_at"] = now()


def inventory(root, path, hash_media=False, kinds=None):
    with transaction(path) as value:
        apply_inventory(value, root, hash_media, kinds)
    return value


def record(root, path, identity, aspect, payload):
    require_studio()
    with transaction(path) as value:
        item = value["items"].get(identity)
        if not item or not item.get("in_current_inventory"):
            raise ValueError("Item is not in the current inventory")
        if aspect not in ASPECTS:
            raise ValueError("Unknown review aspect")
        expected_snapshot = production.expected_snapshot(value, root, item["kind"])
        variant = item["aspects"][aspect]
        if not expected_snapshot["complete"] or not variant["ready_for_review"]:
            raise ValueError("A complete Studio inventory, source clip verification, CTA assignment and output hash are required")
        # Detect a changed file between inventory and record; never silently review a new version.
        try:
            snapshot, _ = production.validate(value, root, item["kind"], _ledger_api())
        except ValueError as exc:
            raise ValueError("Files changed since inventory; rerun inventory and review the exact new output: " + str(exc)) from exc
        output = file_snapshot(root, variant["output"]["path"], True)
        clip = variant["inputs"]["source_clip"]
        source_overrides = overrides.load(root, value.get("teaching_source_overrides"), True) if item["kind"] != "marketing" else {}
        current_rows = dict(rows(root, source_overrides)[0])
        current_row = current_rows.get(identity)
        current_inputs = aspect_input(current_row, aspect, root, True, snapshot["fingerprint"]) if current_row else None
        if snapshot["fingerprint"] != expected_snapshot["fingerprint"] or output != variant["output"] \
                or current_inputs != variant["inputs"] or (clip and file_snapshot(root, clip["path"], True) != clip):
            raise ValueError("Files changed since inventory; rerun inventory and review the exact new output")
        pin = production.family_pin(value, item["kind"])
        if pin and item["kind"] == "marketing" and payload.get("expected_production_root_binding") != digest_json(pin):
            raise ValueError("Explicit judgement must bind the current marketing production root")
        if payload.get("payload_contract") == "explicit-media-judgement/v1":
            if payload.get("expected_input_sha256") != variant["input_sha256"] \
                    or payload.get("expected_output_sha256") != output["sha256"] \
                    or payload.get("expected_source_fingerprint") != snapshot["fingerprint"]:
                raise ValueError("Explicit judgement payload belongs to a different input, output or global source version")
            expected_evidence = payload.get("expected_evidence")
            if not isinstance(expected_evidence, dict) or set(expected_evidence) != set(payload.get("evidence", [])) \
                    or not expected_evidence or any(not checksum or file_snapshot(root, filename, True)["sha256"] != checksum
                        for filename, checksum in expected_evidence.items()):
                raise ValueError("Exact judgement evidence changed after payload export")
        elif payload.get("payload_contract") is not None:
            raise ValueError("Unknown explicit judgement payload contract")
        verdict = payload.get("verdict")
        if verdict not in {"pass", "needs_work", "blocked"}:
            raise ValueError("verdict must be pass, needs_work or blocked")
        for required in ("reviewer", "summary", "evidence", "findings", "repairs", "checks"):
            if required not in payload:
                raise ValueError(f"Missing review field: {required}")
        if not payload["reviewer"].strip() or not payload["summary"].strip() or not payload["evidence"]:
            raise ValueError("Reviewer, summary and non-empty retained evidence are required")
        required_checks = value["policy"]["required_checks"]
        if set(payload["checks"]) != set(required_checks):
            raise ValueError("checks must cover every policy.required_checks entry")
        if verdict == "pass":
            if any(finding.get("status") not in {"fixed", "refuted"} for finding in payload["findings"]):
                raise ValueError("A pass cannot contain unresolved findings")
            if any(payload["checks"][check] not in {"pass", "not_applicable"} for check in required_checks):
                raise ValueError("Every check must explicitly pass or be not_applicable")
            if any(payload["checks"][check] != "pass" for check in required_checks if check != "selected_cta_lip_sync"):
                raise ValueError("Only selected_cta_lip_sync may be not_applicable")
            selected = current_rows[identity if item["kind"] == "cta" else item["cta_id"]]["lip_sync_selected"]
            if selected and payload["checks"]["selected_cta_lip_sync"] != "pass":
                raise ValueError("Selected CTA lip sync must pass")
        evidence = [file_snapshot(root, filename, True) for filename in payload["evidence"]]
        if any(not entry["sha256"] for entry in evidence):
            raise ValueError("Every evidence file must exist")
        if variant["review"]:
            variant["history"].append({"review": variant["review"], "superseded_at": now()})
        variant["review"] = {**payload, "evidence": evidence, "recorded_at": now(),
                             "machine": socket.gethostname(), "input_sha256": variant["input_sha256"],
                             "output_sha256": output["sha256"]}
        variant["state"] = {"pass": "passed", "needs_work": "needs_work", "blocked": "blocked"}[verdict]
        variant.pop("reopen_reason", None)
    return value


def budget_totals(budget):
    reservations = budget["reservations"].values()
    actual = sum(item.get("actual_cents", 0) for item in reservations if item["state"] == "settled")
    reserved = sum(item["maximum_cents"] for item in reservations if item["state"] == "reserved")
    return {"actual_cents": actual, "reserved_cents": reserved,
            "available_cents": budget["cap_cents"] - actual - reserved}


def reserve(path, identity, maximum_cents, category, note):
    if not identity.strip() or maximum_cents <= 0 or not note.strip():
        raise ValueError("A unique reservation ID, positive maximum and note are required")
    if category not in {"image_cleanup", "review", "lip_sync", "voice"}:
        raise ValueError("category must be image_cleanup, review, lip_sync or voice")
    with transaction(path) as value:
        budget = value["budget"]
        if identity in budget["reservations"]:
            raise ValueError("Reservation ID already exists; reconcile it rather than retrying the paid request")
        if maximum_cents > budget_totals(budget)["available_cents"]:
            raise ValueError("Reservation would exceed the US$100 combined budget")
        budget["reservations"][identity] = {"state": "reserved", "maximum_cents": maximum_cents,
            "category": category, "note": note, "reserved_at": now(), "machine": socket.gethostname()}
    return value


def settle(path, identity, actual_cents, receipt):
    if actual_cents < 0 or not receipt.strip():
        raise ValueError("Nonnegative actual cost and a non-secret provider reference or reconciliation note are required")
    with transaction(path) as value:
        reservation = value["budget"]["reservations"].get(identity)
        if reservation is None:
            raise ValueError("Unknown reservation")
        if reservation["state"] == "settled":
            if reservation["actual_cents"] != actual_cents or reservation["receipt"] != receipt:
                raise ValueError("Reservation already settled differently")
            return value
        if actual_cents > reservation["maximum_cents"]:
            raise ValueError("Cost exceeds reserved maximum; do not submit a paid request above its reserved maximum")
        reservation.update({"state": "settled", "actual_cents": actual_cents, "receipt": receipt,
                            "settled_at": now()})
    return value


def dollars(value):
    try:
        cents = Decimal(value) * 100
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("Use a USD amount such as 2.50") from exc
    if not cents.is_finite() or cents != cents.to_integral_value():
        raise argparse.ArgumentTypeError("Use an exact USD amount with at most two decimal places")
    return int(cents)


def register_teaching_override(root, path, filename, expected_sha256, reviewer, reason):
    """Explicit activation, never inferred from a file appearing on disk."""
    require_studio()
    if not reviewer.strip() or not reason.strip():
        raise ValueError("Correction activation needs reviewer and reason")
    relative = portable(root, filename)
    record = read(root / relative)
    skill = record["skillKey"]
    registration = {"path": relative, "sha256": overrides.checksum(expected_sha256)}
    # Validate all source/provenance bytes before acquiring the shared writer lock.
    overrides.load(root, {skill: registration}, True)
    with transaction(path) as value:
        current = value.setdefault("teaching_source_overrides", {})
        existing = current.get(skill)
        if existing and (existing["path"], existing["sha256"]) != (relative, expected_sha256):
            raise ValueError("A correction is already registered; retain it and explicitly migrate separately")
        if not existing:
            current[skill] = {**registration, "registered_at": now(), "reviewer": reviewer, "reason": reason}
        apply_inventory(value, root, True)
    return value


def status(value):
    counts = {}
    for item in value["items"].values():
        if item["in_current_inventory"]:
            for variant in item["aspects"].values():
                counts[variant["state"]] = counts.get(variant["state"], 0) + 1
    return {"inventory": value.get("inventory_counts"), "aspect_states": counts,
            "media_hashes_complete": bool(value["source_snapshot"] and value["source_snapshot"]["complete"]),
            "budget": budget_totals(value["budget"]), "asset_replacement": value["asset_replacement"],
            "freshness": "Last inventory snapshot only. Run inventory --hash-media on Studio to verify current files."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--ledger", type=Path, default=Path("data/media-review-ledger.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory")
    inv.add_argument("--hash-media", action="store_true", help="Studio only: exact media hashes")
    inv.add_argument("--kinds", nargs="+", choices=production.KINDS)
    binding = commands.add_parser("register-marketing-root", help="Studio only: bind an explicitly approved separate marketing build root")
    binding.add_argument("--revision-root", type=Path, required=True)
    binding.add_argument("--cache-map", type=Path, required=True)
    binding.add_argument("--expected-render-fingerprint", required=True)
    binding.add_argument("--reviewer", required=True)
    binding.add_argument("--reason", required=True)
    commands.add_parser("status", help="Report the last inventory, not a new verification")
    rec = commands.add_parser("record")
    rec.add_argument("id")
    rec.add_argument("aspect", choices=ASPECTS)
    rec.add_argument("--file", type=Path, required=True, help="JSON review payload")
    res = commands.add_parser("reserve")
    res.add_argument("id")
    res.add_argument("--usd", type=dollars, required=True)
    res.add_argument("--category", choices=("image_cleanup", "review", "lip_sync", "voice"), required=True)
    res.add_argument("--note", required=True)
    paid = commands.add_parser("settle")
    paid.add_argument("id")
    paid.add_argument("--usd", type=dollars, required=True)
    paid.add_argument("--receipt", required=True)
    assets = commands.add_parser("assets", help="Attach a portable asset replacement manifest")
    assets.add_argument("manifest")
    correction = commands.add_parser("register-teaching-override", help="Studio only: explicitly activate one authorised correction")
    correction.add_argument("file")
    correction.add_argument("--sha256", required=True)
    correction.add_argument("--reviewer", required=True)
    correction.add_argument("--reason", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    path = args.ledger if args.ledger.is_absolute() else root / args.ledger
    try:
        if args.command == "inventory":
            value = inventory(root, path, args.hash_media, args.kinds)
        elif args.command == "register-marketing-root":
            value = register_marketing_root(root, path, args.revision_root, args.cache_map, args.reviewer, args.reason, args.expected_render_fingerprint)
        elif args.command == "record":
            value = record(root, path, args.id, args.aspect, read(args.file))
        elif args.command == "reserve":
            value = reserve(path, args.id, args.usd, args.category, args.note)
        elif args.command == "settle":
            value = settle(path, args.id, args.usd, args.receipt)
        elif args.command == "assets":
            manifest = file_snapshot(root, args.manifest, True)
            if not manifest["sha256"]:
                raise ValueError("Asset replacement manifest does not exist")
            with transaction(path) as value:
                value["asset_replacement"].update({"state": "manifest_attached", "manifest": manifest["path"],
                                                   "manifest_sha256": manifest["sha256"]})
        elif args.command == "register-teaching-override":
            value = register_teaching_override(root, path, args.file, args.sha256, args.reviewer, args.reason)
        else:
            value = read(path)
        print(json.dumps(status(value), indent=2))
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"review-ledger: {exc}\n")


if __name__ == "__main__":
    main()
