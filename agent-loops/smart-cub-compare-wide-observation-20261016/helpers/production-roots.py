"""Fixed marketing production family and exact historical evidence copies.

This is an explicit second build root, never an assertion of media equivalence.
CTA/teaching identities remain in the legacy canonical snapshot unchanged.
"""
from pathlib import Path
import hashlib
import json

KINDS = ("marketing", "cta", "teaching_social_derivative")
RENDER_FILES = ("data/scripts.json", "data/ctas.json", "data/cta-assignments.json", "data/avatars.json",
                "package.json", "package-lock.json", "tsconfig.json", "scripts/render.ts",
                "scripts/render-batch.ts", "scripts/render-batch-support.ts")


def pinned_path(value):
    path = Path(value)
    if not path.is_absolute() or str(path) != str(path.resolve()):
        raise ValueError("Production root/evidence path must be absolute, normalised and without symlinks")
    # resolve() alone does not reject symlinks pointing to the same spelling.
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Production root/evidence symlinks are forbidden")
    return path


def render_snapshot(root, ledger):
    paths = set()
    def walk(path):
        if path.is_symlink():
            raise ValueError("Symlinked render source")
        if path.is_dir():
            for child in path.iterdir():
                walk(child)
        elif path.is_file():
            paths.add(path.relative_to(root).as_posix())
    for name in ("src", "public", "data/words", "data/durations", *RENDER_FILES):
        walk(root / name)
    files = [{key: entry[key] for key in ("path", "bytes", "sha256")}
             for entry in (ledger.file_snapshot(root, name, True) for name in sorted(paths))]
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {"files": files, "fingerprint": digest}


def binding(value):
    registrations = value.get("production_roots") or {}
    if set(registrations) - {"marketing"}:
        raise ValueError("Only the fixed marketing production root is supported")
    result = registrations.get("marketing")
    if result is not None:
        if result.get("contract") != "marketing-production-root/v1" or result.get("kind") != "marketing":
            raise ValueError("Invalid marketing production binding")
        canonical, marketing = pinned_path(result["canonical_root"]), pinned_path(result["root"])
        if canonical == marketing or canonical.is_relative_to(marketing) or marketing.is_relative_to(canonical):
            raise ValueError("Production roots must be separate project directories")
    return result


def root_for(value, canonical, kind):
    if kind not in KINDS:
        raise ValueError("Unknown production family")
    bound = binding(value)
    if not bound:
        return canonical.resolve()
    canonical = pinned_path(canonical)
    if canonical != Path(bound["canonical_root"]):
        raise ValueError("Wrong canonical production root")
    return Path(bound["root"] if kind == "marketing" else bound["canonical_root"])


def kind_for_root(value, root):
    bound = binding(value)
    if not bound:
        return None
    root = pinned_path(root)
    if root == Path(bound["root"]):
        return "marketing"
    if root == Path(bound["canonical_root"]):
        return "canonical"
    raise ValueError("Wrong configured production root")


def family_pin(value, kind):
    bound = binding(value)
    if not bound:
        return None
    return bound if kind == "marketing" else {"canonical_root": bound["canonical_root"]}


def expected_snapshot(value, root, kind):
    family = kind_for_root(value, root)
    if family and (family == "marketing") != (kind == "marketing"):
        raise ValueError("Item kind belongs to a different production root")
    return binding(value)["source_snapshot"] if family == "marketing" else value.get("source_snapshot")


def validate(value, root, kind, ledger):
    expected = expected_snapshot(value, root, kind)
    actual = ledger.source_snapshot(root, True, value["asset_replacement"]["manifest"])
    if not expected or not expected.get("complete") or actual["fingerprint"] != expected["fingerprint"]:
        raise ValueError("A current complete Studio ledger inventory is required for this production family")
    rendered = render_snapshot(root, ledger)
    bound = binding(value)
    if bound:
        canonical = Path(bound["canonical_root"])
        if (value.get("source_snapshot") or {}).get("fingerprint") != bound["canonical_source_fingerprint"]:
            raise ValueError("Canonical family registry/source authority changed")
        if kind != "marketing" and rendered != bound["canonical_render_snapshot"]:
            raise ValueError("Canonical render source differs from its approved family binding")
        if kind == "marketing":
            old = ledger.source_snapshot(canonical, True, value["asset_replacement"]["manifest"])
            if old["fingerprint"] != bound["canonical_source_fingerprint"] \
                    or render_snapshot(canonical, ledger) != bound["canonical_render_snapshot"]:
                raise ValueError("Canonical family registry/source changed")
            if rendered != bound["render_snapshot"]:
                raise ValueError("Marketing render source differs from its approved family binding")
    return actual, rendered


def cache_map(value, ledger):
    bound = binding(value)
    if not bound or not bound.get("historical_cache_map"):
        return {}
    pin = bound["historical_cache_map"]
    manifest_path = pinned_path(pin["path"])
    if ledger.sha256(manifest_path) != pin["sha256"]:
        raise ValueError("Historical cache-copy map changed")
    manifest = ledger.read(manifest_path)
    canonical, root = Path(bound["canonical_root"]), Path(bound["root"])
    if manifest.get("contract") != "historical-marketing-cache-copy-map/v1" \
            or manifest.get("scope") != "historical-marketing-evidence-only" \
            or manifest.get("source_root") != str(canonical) or manifest.get("copy_root") != str(root) \
            or manifest.get("source_cache_root") != str(canonical / "out/review-complete") \
            or manifest.get("copy_cache_root") != str(root / "out/review-complete"):
        raise ValueError("Historical cache-copy map has wrong roots or scope")
    allowed = {row["id"] for row in ledger.read(root / "data/scripts.json")}
    if set(manifest.get("allowed_marketing_ids", [])) != allowed:
        raise ValueError("Historical cache-copy map marketing registry differs")
    result, copies = {}, set()
    for item in manifest.get("files", []):
        old = pinned_path(item["old_absolute"])
        try:
            suffix = old.relative_to(canonical / "out/review-complete")
        except ValueError as exc:
            raise ValueError("Historical cache map cannot remap current source, media or receipts") from exc
        if len(suffix.parts) < 4 or suffix.parts[0] not in allowed or suffix.parts[1] not in {"tall", "wide"} \
                or suffix.suffix not in {".json", ".txt", ".log", ".lock"}:
            raise ValueError("Historical cache map contains non-marketing or non-evidence files")
        relative = ledger.portable(root, item["copy_relative"])
        if relative != (Path("out/review-complete") / suffix).as_posix() \
                or item["old_absolute"] in result or relative in copies:
            raise ValueError("Historical cache map path substitution or duplicate")
        pinned_path(root / relative)
        if suffix.suffix == ".lock":
            continue  # Copied lock bytes are provenance, never admissible evidence.
        result[item["old_absolute"]] = item
        copies.add(relative)
    return result


def historical_copy(value, filename, ledger, expected=None, identity=None, aspect=None, mapping=None):
    """Only evidence callers may use this. Never call for source/media/receipts."""
    pin = binding(value)["historical_cache_map"]
    if ledger.sha256(pinned_path(pin["path"])) != pin["sha256"]:
        raise ValueError("Historical cache-copy map changed")
    item = (mapping if mapping is not None else cache_map(value, ledger)).get(str(filename))
    if not item:
        raise ValueError("Unmapped historical evidence outside the production root")
    bound = binding(value)
    suffix = Path(item["copy_relative"]).relative_to("out/review-complete")
    if (identity is not None and suffix.parts[0] != identity) or (aspect is not None and suffix.parts[1] != aspect):
        raise ValueError("Historical evidence belongs to another item/aspect")
    if expected is not None and expected != item["sha256"]:
        raise ValueError("Historical evidence expected hash differs")
    original, copy = pinned_path(item["old_absolute"]), pinned_path(Path(bound["root"]) / item["copy_relative"])
    for path in (original, copy):
        if not path.is_file() or path.stat().st_size != item["bytes"] or ledger.sha256(path) != item["sha256"]:
            raise ValueError("Historical evidence original or copy bytes changed")
    return copy


def validate_binding(value, result, ledger):
    """Re-prove actual root/cache bytes at the registration mutation boundary."""
    trial = {**value, "production_roots": {"marketing": result}}
    binding(trial)
    validate(trial, Path(result["root"]), "marketing", ledger)
    mapping = cache_map(trial, ledger)
    for filename in mapping:
        historical_copy(trial, filename, ledger, mapping=mapping)
    return result


def build(value, canonical, root, manifest, reviewer, reason, ledger, expected_render_fingerprint=None):
    ledger.require_studio()
    canonical, root, manifest = pinned_path(canonical), pinned_path(root), pinned_path(manifest)
    if binding(value):
        raise ValueError("A marketing production root is already registered; no implicit replacement")
    if not reviewer.strip() or not reason.strip():
        raise ValueError("Explicit production-root reviewer and reason required")
    canonical_source, canonical_render = validate(value, canonical, "cta", ledger)
    source = ledger.source_snapshot(root, True, value["asset_replacement"]["manifest"])
    result = {"contract": "marketing-production-root/v1", "kind": "marketing", "root": str(root),
              "canonical_root": str(canonical), "canonical_source_fingerprint": canonical_source["fingerprint"],
              "canonical_render_snapshot": canonical_render, "source_snapshot": source,
              "render_snapshot": render_snapshot(root, ledger), "reviewer": reviewer, "reason": reason,
              "registered_at": ledger.now(), "historical_cache_map": {"path": str(manifest), "sha256": ledger.sha256(manifest)}}
    if expected_render_fingerprint is not None and result["render_snapshot"]["fingerprint"] != expected_render_fingerprint:
        raise ValueError("Marketing render source differs from the explicitly approved fingerprint")
    return validate_binding(value, result, ledger)
