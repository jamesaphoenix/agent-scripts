#!/usr/bin/env python3
"""Prepare one pending review reconciliation after an explicitly rerendered graph.

No paid call, rendering, archive creation, final verdict or ledger write. Historical
observations apply only when actual output bytes and approved context are identical.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re

SPEC = importlib.util.spec_from_file_location("reconcile_prepare", Path(__file__).with_name("prepare-review.py"))
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)
ledger = prepare.ledger
CONTRACT = "pending-review-reconciliation/v1"


def archive_entries(root, filename):
    if filename is None:
        return {}, []
    artifact = prepare.exact(root, filename)
    manifest = ledger.read(root / artifact["path"])
    if manifest.get("contract") != "review-evidence-archive/v1":
        raise ValueError("Unsupported exact-byte archive manifest")
    entries = {}
    for row in manifest.get("entries", []):
        original = prepare.relative(root, row["original_path"])
        archived = prepare.relative(root, row["archive_path"])
        key = (original, row["sha256"])
        if key in entries or original == archived:
            raise ValueError("Duplicate or non-archived historical evidence")
        entries[key] = archived
    return entries, [artifact]


def archived_receipt(preparation, original, old_sha, archived):
    """Verify both receipt generations and actual old bytes, never synthesise proof."""
    root = preparation.root
    match = re.fullmatch(r"out/(.+)-(tall|wide)\.render-receipt\.json", original)
    if not match or match[1] not in preparation.value["items"] \
            or preparation.value["items"][match[1]]["kind"] not in {"marketing", "cta"}:
        raise ValueError("Only known render receipts may be resolved through an archive")
    identity, aspect = match.groups()
    old_file = prepare.exact(root, archived, old_sha)
    old = ledger.read(root / old_file["path"])
    source = old.get("compositionSource", {})
    deps = old.get("dependencies", {})
    fingerprint = source.get("fingerprint")
    if old.get("status") != "rendered-needs-review" or source.get("scope") != "global-fallback" \
            or prepare.js_digest(source.get("files")) != fingerprint \
            or old.get("frozenBatchSourceSnapshot") != fingerprint \
            or deps.get("sourceSnapshot") != fingerprint or deps.get("sourceScope") != "global-fallback" \
            or deps.get("composition", {}).get("id") != f"{identity}-{aspect}" \
            or prepare.js_digest(deps) != old.get("dependencyHash"):
        raise ValueError("Archived render receipt is internally inconsistent")
    snapshot_file = prepare.exact(root, old["sourceSnapshotPath"])
    if ledger.read(root / snapshot_file["path"]) != {"files": source["files"], "fingerprint": fingerprint}:
        raise ValueError("Archived receipt lost its original exact source snapshot")
    output = prepare.exact(root, f"out/{identity}-{aspect}.mp4")
    if old.get("output") != {k: output[k] for k in ("path", "bytes", "sha256")}:
        raise ValueError("Historical supporting output differs from current bytes; new observation required")
    current = preparation.render_receipt(identity, aspect)
    return [old_file, snapshot_file, *current], {"original_path": original, "old_sha256": old_sha,
        "archived": old_file, "current": current[0], "old_render_fingerprint": fingerprint,
        "new_render_fingerprint": preparation.render_source["fingerprint"]}


def reuse_log(root, filename, candidate):
    if filename is None:
        raise ValueError("Actual saved existing review CLI cache-reuse log is required")
    artifact = prepare.exact(root, filename)
    records = [json.loads(line) for line in (root / artifact["path"]).read_text().splitlines() if line.strip()]
    matches = [row for row in records if row.get("id") == candidate["id"] and row.get("aspect") == candidate["aspect"]]
    if len(matches) != 1 or matches[0].get("state") != "skipped_exact_version_advisory" \
            or matches[0].get("paid_requests") != 0 \
            or prepare.relative(root, matches[0].get("evidence", "")) != candidate["job_path"]:
        raise ValueError("CLI log does not prove one exact target/job advisory cache hit with zero paid requests")
    return artifact


def build(preparation, candidate_path, prior_payload_path, note_path, archive_manifest=None, cache_reuse_log=None):
    root = preparation.root
    candidate_file = prepare.exact(root, candidate_path)
    candidate = ledger.read(root / candidate_file["path"])
    if preparation.candidate(candidate["id"], candidate["aspect"], candidate["job_path"]) != candidate:
        raise ValueError("New candidate does not prove the current exact output and frozen graph")
    previous_file = prepare.exact(root, prior_payload_path)
    previous = ledger.read(root / previous_file["path"])
    decision = previous.get("explicit_judgement", {})
    required = preparation.value["policy"]["required_checks"]
    if previous.get("payload_contract") != prepare.CONTRACT or previous.get("verdict") != "pass" \
            or decision.get("verdict") != "pass" or not previous.get("reviewer", "").strip() \
            or set(previous.get("checks", {})) != set(required) \
            or any(v not in {"pass", "not_applicable"} for v in previous["checks"].values()) \
            or any(f.get("status") not in {"fixed", "refuted"} for f in previous.get("findings", [])):
        raise ValueError("Previous artifact must contain an explicit accepted judgement, not model advice")
    expected = previous.get("expected_evidence", {})
    if not expected or set(expected) != set(previous.get("evidence", [])):
        raise ValueError("Previous payload lacks complete exact evidence bindings")
    old_candidates = []
    for path, sha in expected.items():
        if path.endswith(".candidate.json"):
            file = prepare.exact(root, path, sha)
            value = ledger.read(root / file["path"])
            if value.get("id") == candidate["id"] and value.get("aspect") == candidate["aspect"] \
                    and value.get("input_sha256") == previous.get("expected_input_sha256") \
                    and value.get("source_fingerprint") == previous.get("expected_source_fingerprint"):
                old_candidates.append((file, value))
    if len(old_candidates) != 1:
        raise ValueError("Require one exact primary historical candidate for this identity and version")
    old_file, old = old_candidates[0]
    if decision.get("candidate_sha256") != old_file["sha256"] \
            or old["output"]["sha256"] != previous.get("expected_output_sha256") \
            or old["output"] != candidate["output"]:
        raise ValueError("Output identity changed; require a new complete observation and judgement")
    note_file = prepare.exact(root, note_path)
    if expected.get(note_file["path"]) != note_file["sha256"]:
        raise ValueError("Immutable note was not retained by the accepted historical judgement")
    note = ledger.read(root / note_file["path"])
    if (note.get("id"), note.get("aspect"), note.get("outputSha256")) != \
            (candidate["id"], candidate["aspect"], candidate["output"]["sha256"]):
        raise ValueError("Immutable per-output note belongs to different bytes or identity")
    old_job_path = old["job_path"]
    if old_job_path not in expected:
        raise ValueError("Previous primary advisory was not retained as exact evidence")
    old_job_file = prepare.exact(root, old_job_path, expected.get(old_job_path))
    old_job = ledger.read(root / old_job_file["path"])
    if old_job.get("state") != "completed_advisory" or old_job.get("version", {}).get("output_sha256") != old["output"]["sha256"]:
        raise ValueError("Historical primary advisory belongs to different output bytes")
    current_job = ledger.read(root / candidate["job_path"])
    context_file = prepare.exact(root, old_job["context_path"], old_job["context_file_sha256"])
    context = ledger.read(root / context_file["path"])
    current_context = ledger.read(Path(current_job["context_path"]))
    if old_job.get("id") != candidate["id"] or old_job.get("aspect") != candidate["aspect"] \
            or old_job.get("version", {}).get("context_sha256") != ledger.digest_json(context) \
            or context != current_context:
        raise ValueError("Approved context changed; historical observations cannot be reconciled")
    cache_log = reuse_log(root, cache_reuse_log, candidate)
    archive, archive_evidence = archive_entries(root, archive_manifest)
    retained, supersessions = [], []
    for path, sha in expected.items():
        actual = ledger.file_snapshot(root, path, True)
        if actual["sha256"] == sha:
            retained.append(prepare.exact(root, path, sha))
        elif (path, sha) in archive:
            files, supersession = archived_receipt(preparation, path, sha, archive[path, sha])
            retained += files
            supersessions.append(supersession)
        else:
            raise ValueError("Historical evidence changed without exact immutable archive proof: " + path)
    restriction = decision.get("distribution_restrictions")
    if note.get("requiredDistributionRestriction") != restriction:
        raise ValueError("Historical judgement and immutable note disagree on distribution restriction")
    evidence = [candidate_file, previous_file, old_file, note_file, old_job_file, context_file, cache_log,
                *candidate["evidence"], *retained, *archive_evidence]
    evidence = list({entry["path"]: entry for entry in evidence}.values())
    packet = {"contract": CONTRACT, "state": "explicit_judgement_pending", "id": candidate["id"],
        "aspect": candidate["aspect"], "verdict": None, "publication_approval": False,
        "current_candidate": candidate_file, "previous_payload": previous_file, "previous_candidate": old_file,
        "immutable_note": note_file, "cache_reuse_cli_log": cache_log,
        "cache_reuse_provenance": "Saved stdout of existing review-media.py CLI; helper made no call and invented no cache hit.",
        "output_sha256": candidate["output"]["sha256"],
        "approved_context_sha256": ledger.digest_json(context),
        "old_source_fingerprint": old["source_fingerprint"], "current_source_fingerprint": candidate["source_fingerprint"],
        "receipt_supersessions": supersessions, "distribution_restrictions": restriction,
        "historical_judgement_for_comparison_only": decision, "evidence": evidence,
        "required_reviewer_work": ["Read the exact immutable note, full current advisory and technical findings.",
            "Compare current receipt, output/context identity and source-copy audit/addenda.",
            "Judge every current check, coverage warning and issue independently; retain dated restrictions and observation limits.",
            "Use only existing review-media.py CLI for advisory-cache reuse; this packet never submits or reuses a provider call.",
            "Finalize through prepare-review.py and explicitly record only this output after judgement."]}
    template = prepare.decision_template(candidate, candidate_file["sha256"])
    for judgement in [*template["checks"].values(), template["playback"],
                      *template["coverage_reconciliations"].values(), *template["dispositions"].values()]:
        judgement["evidence"] = evidence.copy()
    if restriction is not None:
        template["distribution_restrictions"] = restriction
    preparation.stable()
    return packet, template


def immutable(path, value):
    if path.exists():
        if ledger.read(path) != value:
            raise ValueError("Refusing to overwrite immutable reconciliation artifact")
    else:
        prepare.atomic_json(path, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=prepare.ROOT)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--prior-payload", type=Path, required=True)
    parser.add_argument("--note", type=Path, required=True)
    parser.add_argument("--archive-manifest", type=Path)
    parser.add_argument("--cache-reuse-log", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("out/review-reconciliations"))
    args = parser.parse_args()
    try:
        prepared = prepare.Preparation(args.root.resolve(), args.ledger.expanduser())
        packet, template = build(prepared, args.candidate, args.prior_payload, args.note, args.archive_manifest, args.cache_reuse_log)
        directory = prepared.root / prepare.relative(prepared.root, args.output_dir)
        name = f"{packet['id'].replace(':', '_')}-{packet['aspect']}-{ledger.digest_json(packet)[:20]}"
        packet_path = directory / (name + ".reconciliation.json")
        immutable(packet_path, packet)
        packet_file = prepare.exact(prepared.root, packet_path)
        template["prior_review_reconciliation"] = packet_file
        for judgement in [*template["checks"].values(), template["playback"],
                          *template["coverage_reconciliations"].values(), *template["dispositions"].values()]:
            judgement["evidence"].append(packet_file)
        template_path = directory / (name + ".decision-template.json")
        immutable(template_path, template)
        prepared.stable()
        print(json.dumps({"state": "explicit_judgement_pending", "packet": prepare.relative(prepared.root, packet_path),
                          "decision_template": prepare.relative(prepared.root, template_path), "ledger_written": False}))
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"reconcile-review: {exc}\n")


if __name__ == "__main__":
    main()
