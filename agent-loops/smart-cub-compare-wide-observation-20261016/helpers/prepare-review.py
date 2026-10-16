#!/usr/bin/env python3
"""Prepare exact-byte review candidates, then export explicitly judged payloads.

No provider, renderer or ledger writer runs. prepare/finalize hash media on Studio.
Candidates intentionally have null verdicts and pending checks. Copy the decision
template into a separate JSON file, supply real evidence and judge each check.
The final payload must still be submitted with review-ledger.py record.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import tempfile


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ledger = module("candidate_ledger", "review-ledger.py")
media = module("candidate_media", "review-media.py")
ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "explicit-media-judgement/v1"
RENDER_FILES = ledger.production.RENDER_FILES


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output, indent=2, ensure_ascii=False, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def relative(root, filename):
    path = Path(filename)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("Retained evidence must be inside the project") from exc
    return ledger.portable(root, path)


def exact(root, filename, expected=None):
    value = ledger.file_snapshot(root, relative(root, filename), True)
    if not value["sha256"] or (expected is not None and value["sha256"] != expected):
        raise ValueError("Missing or changed exact-byte evidence: " + value["path"])
    return value


def js_digest(value):
    # SourceSnapshotter records contain only strings and integer byte lengths.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def render_snapshot(root):
    return ledger.production.render_snapshot(root, ledger)


class Preparation:
    def __init__(self, root, authority):
        ledger.require_studio()
        self.root, self.authority = root.resolve(), authority.resolve()
        self.value = ledger.read(authority)
        self.family = ledger.production.kind_for_root(self.value, self.root)
        self.kind = "marketing" if self.family == "marketing" else "cta"
        self._family_preparations = {}
        self.historical_map = ledger.production.cache_map(self.value, ledger) if self.family == "marketing" else {}
        self.historical_destinations = {entry["copy_relative"]: entry for entry in self.historical_map.values()}
        self.family_pin = ledger.production.family_pin(self.value, self.kind)
        self.global_source, self.render_source = ledger.production.validate(self.value, self.root, self.kind, ledger)
        self.source_overrides = (ledger.overrides.load(root, self.value.get("teaching_source_overrides"), True)
                                 if self.family != "marketing" else {})
        self.current_rows = dict(ledger.rows(root, self.source_overrides)[0])
        self.review_items = media.inventory(root, root / "../../web/content/skill-videos/published.json")
        if self.source_overrides:
            self.review_items = ledger.overrides.review_items(self.review_items, self.source_overrides,
                                                              ledger.overrides.implementation(root))

    def for_kind(self, kind):
        if not ledger.production.binding(self.value):
            return self
        canonical = Path(ledger.production.binding(self.value)["canonical_root"])
        target = ledger.production.root_for(self.value, canonical, kind)
        if target == self.root:
            return self
        if target not in self._family_preparations:
            self._family_preparations[target] = Preparation(target, self.authority)
        return self._family_preparations[target]

    def stable(self):
        current_ledger = ledger.read(self.authority)
        if ledger.production.family_pin(current_ledger, self.kind) != self.family_pin:
            raise ValueError("Production family authority changed during preparation")
        current, rendered = ledger.production.validate(current_ledger, self.root, self.kind, ledger)
        if current["fingerprint"] != self.global_source["fingerprint"] or rendered != self.render_source:
            raise ValueError("Global source changed while preparing judgement; discard the candidate")
        if self.family != "marketing" and ledger.overrides.load(self.root, current_ledger.get("teaching_source_overrides"), True) != self.source_overrides:
            raise ValueError("Registered correction authority changed during preparation")

    def advisory_artifact(self, filename, expected=None, identity=None, aspect=None):
        path = Path(filename)
        # Bound marketing historical paths must be checked lexically before
        # resolve(), otherwise an old path symlinked into the revision bypasses
        # its original/copy proof. Legacy canonical paths retain macOS aliases.
        outside = (not path.is_relative_to(self.root) if self.family == "marketing"
                   else not path.resolve().is_relative_to(self.root)) if path.is_absolute() else False
        if outside:
            if self.family != "marketing":
                raise ValueError("Historical evidence mapping is restricted to the marketing family")
            path = ledger.production.historical_copy(self.value, filename, ledger, expected, identity, aspect, self.historical_map)
        return exact(self.root, path, expected)

    def advisory_path(self, filename, expected=None, identity=None, aspect=None):
        return self.root / self.advisory_artifact(filename, expected, identity, aspect)["path"]

    def render_receipt(self, identity, aspect):
        output = f"out/{identity}-{aspect}.mp4"
        receipt_file = f"out/{identity}-{aspect}.render-receipt.json"
        receipt = ledger.read(self.root / receipt_file)
        source = receipt.get("compositionSource", {})
        dependencies = receipt.get("dependencies", {})
        if receipt.get("status") != "rendered-needs-review" \
                or source.get("scope") != "global-fallback" \
                or source.get("files") != self.render_source["files"] \
                or source.get("fingerprint") != self.render_source["fingerprint"] \
                or receipt.get("frozenBatchSourceSnapshot") != self.render_source["fingerprint"] \
                or dependencies.get("sourceSnapshot") != self.render_source["fingerprint"] \
                or dependencies.get("sourceScope") != "global-fallback" \
                or dependencies.get("composition", {}).get("id") != f"{identity}-{aspect}" \
                or js_digest(dependencies) != receipt.get("dependencyHash"):
            raise ValueError("Render receipt does not prove the current complete source graph: " + receipt_file)
        actual = exact(self.root, output, receipt.get("output", {}).get("sha256"))
        if receipt["output"].get("path") != output or receipt["output"].get("bytes") != actual["bytes"] \
                or receipt["output"].get("sha256") != actual["sha256"]:
            raise ValueError("Render receipt output identity differs")
        snapshot_file = relative(self.root, receipt["sourceSnapshotPath"])
        frozen = ledger.read(self.root / snapshot_file)
        if frozen != self.render_source:
            raise ValueError("Retained render snapshot differs from current complete sources")
        return [exact(self.root, receipt_file), exact(self.root, snapshot_file)]

    def blend_receipt(self, item, aspect, variant):
        if item.get("source_override"):
            return self.corrected_blend_receipt(item, aspect, variant)
        path = variant["output"]["path"]
        receipt_path = str(Path(path).with_suffix(".receipt.json"))
        receipt = ledger.read(self.root / receipt_path)
        dependencies = receipt.get("dependencies", {})
        context_row = self.review_items[item["id"]]["context"]["source_row"]
        source = context_row["media"][aspect]
        source_clip = variant["inputs"]["source_clip"]
        cta_path = f"out/{item['cta_id']}-{aspect}.mp4"
        cta = exact(self.root, cta_path)
        if receipt.get("status") != "rendered-needs-review" or receipt.get("skillKey") != item["skill_key"] \
                or dependencies.get("source") != source or source["sha256"] != source_clip["sha256"] \
                or dependencies.get("ctaId") != item["cta_id"] or dependencies.get("aspect") != aspect \
                or dependencies.get("ctaSha256") != cta["sha256"] \
                or dependencies.get("scriptSha256") != ledger.sha256(self.root / "scripts/blend-teaching.py") \
                or ledger.digest_json(dependencies) != receipt.get("dependencyHash") \
                or relative(self.root, receipt.get("sourcePath", "")) != source_clip["path"] \
                or relative(self.root, receipt.get("ctaPath", "")) != cta_path \
                or relative(self.root, receipt.get("output", {}).get("path", "")) != path \
                or receipt.get("output", {}).get("sha256") != variant["output"]["sha256"]:
            raise ValueError("Teaching blend receipt does not match current source, CTA, manifest or output")
        manifest = exact(self.root, "data/teaching-social-inputs.json")
        # Separate final-blend evidence is deliberately mandatory. Original lesson
        # approval, concatenation success or a CTA-only review cannot supply it.
        verification_path = str(Path(path).with_suffix(".blend-verification.json"))
        verification = ledger.read(self.root / verification_path)
        required = {"complete_source_preserved", "cta_join_verified", "picture_and_audio_verified"}
        if verification.get("output_sha256") != variant["output"]["sha256"] \
                or verification.get("source_sha256") != source_clip["sha256"] \
                or verification.get("cta_sha256") != cta["sha256"] \
                or verification.get("manifest_sha256") != manifest["sha256"] \
                or set(verification.get("checks", {})) != required \
                or any(verification["checks"][key] is not True for key in required) \
                or not verification.get("reviewer", "").strip() or not verification.get("rationale", "").strip() \
                or not verification.get("evidence"):
            raise ValueError("Final teaching blend verification is incomplete or stale")
        artifacts = evidence_for(self.root, verification["evidence"])
        return [exact(self.root, receipt_path), manifest, exact(self.root, verification_path), *artifacts,
                *self.render_receipt(item["cta_id"], aspect)]

    def corrected_blend_receipt(self, item, aspect, variant):
        correction = module("prepare_corrected_blend", "blend-corrected-teaching.py")
        proof = correction.preflight_output(self.root, self.authority, item["skill_key"], aspect)
        override = item["source_override"]
        output = variant["output"]["path"]
        source = variant["inputs"]["source_clip"]
        if output != override["record"]["outputPaths"][aspect] \
                or source["path"] != override["record"]["media"][aspect]["path"] \
                or source["sha256"] != proof["sourceSha256"] \
                or variant["output"]["sha256"] != proof["outputSha256"]:
            raise ValueError("Corrected inventory differs from current exact blend")
        verification_path = str(Path(output).with_suffix(".blend-verification.json"))
        verification = ledger.read(self.root / verification_path)
        required = {"complete_source_preserved", "cta_join_verified", "picture_and_audio_verified"}
        expected = {"contract": "teaching-corrected-source-blend/v1", "output_sha256": proof["outputSha256"],
            "source_sha256": proof["sourceSha256"], "corrected_source_sha256": proof["sourceSha256"],
            "original_source_sha256": proof["originalSourceSha256"], "cta_sha256": proof["ctaSha256"],
            "manifest_sha256": proof["manifestSha256"], "source_override_sha256": override["sha256"]}
        if any(verification.get(k) != v for k, v in expected.items()) \
                or set(verification.get("checks", {})) != required \
                or any(verification["checks"][key] is not True for key in required) \
                or not verification.get("reviewer", "").strip() or not verification.get("rationale", "").strip() \
                or not verification.get("evidence"):
            raise ValueError("Explicit corrected-source final blend verification is incomplete or stale")
        files = [str(Path(output).with_suffix(suffix)) for suffix in
                 (".receipt.json", ".mechanical-verification.json", ".blend-verification.json")]
        return [*(exact(self.root, p) for p in files), exact(self.root, override["path"], override["sha256"]),
                exact(self.root, "data/teaching-social-inputs.json"),
                *(exact(self.root, p["path"], p["sha256"]) for p in override["record"]["provenance"]),
                *evidence_for(self.root, verification["evidence"]), *self.render_receipt(item["cta_id"], aspect)]

    def candidate(self, identity, aspect, job_path):
        item = self.value["items"].get(identity)
        if not item or not item.get("in_current_inventory"):
            raise ValueError("Unknown or retired inventory item")
        ledger.production.expected_snapshot(self.value, self.root, item["kind"])
        if aspect not in ledger.ASPECTS:
            raise ValueError("Unknown review aspect")
        item = {**item, "id": identity}
        variant = item["aspects"][aspect]
        if not variant.get("ready_for_review"):
            raise ValueError("Output is not ready for exact-version review")
        current = self.current_rows.get(identity)
        inputs = ledger.aspect_input(current, aspect, self.root, True, self.global_source["fingerprint"])
        if inputs != variant["inputs"] or ledger.digest_json(inputs) != variant["input_sha256"] \
                or exact(self.root, variant["output"]["path"]) != variant["output"]:
            raise ValueError("Inventory input or output bytes changed; rerun inventory")
        review_item = self.review_items[identity]
        if review_item["outputs"][aspect] != variant["output"]["path"]:
            raise ValueError("Review, blend and ledger output mappings disagree")
        job_path = relative(self.root, job_path)
        if job_path in self.historical_destinations:
            original = self.historical_destinations[job_path]
            ledger.production.historical_copy(self.value, original["old_absolute"], ledger,
                                              original["sha256"], identity, aspect, self.historical_map)
        job = ledger.read(self.root / job_path)
        media.policy.require_explicit_job(self.root, self.expected_request, self.expected_request_sha256, job_path, job, media)
        version = job.get("version", {})
        fields = (("response_path", "response_sha256"), ("technical_evidence", "technical_evidence_sha256"),
                  ("context_path", "context_file_sha256"), ("prompt_path", "prompt_file_sha256"),
                  ("request_body_path", "request_body_file_sha256"))
        # Validate BOTH copies before any legacy routine dereferences old absolute
        # paths. This resolver is used only for advisory evidence, never receipts.
        try:
            artifacts = [exact(self.root, job_path)]
            for key, checksum in fields:
                artifacts.append(self.advisory_artifact(job[key], job[checksum], identity, aspect))
            artifacts += [self.advisory_artifact(entry["path"], entry["sha256"], identity, aspect)
                          for entry in job["technical_artifacts"]]
        except ValueError as exc:
            raise ValueError("Completed exact-byte advisory evidence is required: " + str(exc)) from exc
        if job.get("id") != identity or job.get("aspect") != aspect \
                or job.get("state") != "completed_advisory" \
                or not media.current_review(job, job.get("version_key")) \
                or version.get("output_sha256") != variant["output"]["sha256"] \
                or version.get("context_sha256") != ledger.digest_json(review_item["context"]):
            raise ValueError("Completed exact-byte advisory and current approved context are required")
        context = ledger.read(self.advisory_path(job["context_path"], job["context_file_sha256"], identity, aspect))
        if context != review_item["context"]:
            raise ValueError("Saved model context differs from current approved source")
        technical = ledger.read(self.advisory_path(job["technical_evidence"], job["technical_evidence_sha256"], identity, aspect))
        old_probe_path = Path(job["technical_evidence"]).parent / "ffprobe.json"
        probe_path = self.advisory_path(old_probe_path, technical["ffprobe_sha256"], identity, aspect)
        log_path = self.advisory_path(old_probe_path.with_name("ffmpeg.log"), technical["ffmpeg_log_sha256"], identity, aspect)
        if technical.get("output_sha256") != variant["output"]["sha256"] \
                or not media.technical_current(technical, Path(job["technical_evidence"]).parent,
                                               version.get("implementation_sha256"), variant["output"]["sha256"]):
            raise ValueError("Technical evidence belongs to different bytes or implementation")
        probe = ledger.read(probe_path)
        duration = float(probe.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0 or abs(duration - technical["duration_seconds"]) > 0.001:
            raise ValueError("Technical and retained ffprobe duration disagree")
        if technical["state"] == "technical_clear" and any(technical.get(key) is not True for key in
                ("dimensions_ok", "video_present", "audio_present", "full_decode_ok")):
            raise ValueError("Technical clearance contradicts its complete decode checks")
        response = ledger.read(self.advisory_path(job["response_path"], job["response_sha256"], identity, aspect))
        candidates = response.get("candidates", [])
        if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise ValueError("Blocked or truncated provider response is not completed advisory evidence")
        text = "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", []) if not part.get("thought"))
        advisory = json.loads(text)
        media.validate_result(advisory, technical["duration_seconds"])
        if advisory != job.get("advisory"):
            raise ValueError("Saved advisory differs from the exact provider response")
        artifacts += [exact(self.root, probe_path, technical["ffprobe_sha256"]),
                      exact(self.root, log_path, technical["ffmpeg_log_sha256"])]
        if item["kind"] == "teaching_social_derivative":
            artifacts += self.blend_receipt(item, aspect, variant)
        else:
            artifacts += self.render_receipt(identity, aspect)
        if self.value["asset_replacement"].get("manifest"):
            artifacts.append(exact(self.root, self.value["asset_replacement"]["manifest"],
                                   self.value["asset_replacement"]["manifest_sha256"]))
        warnings = []
        if not advisory["observed_complete"]:
            warnings.append({"id": "incomplete-model-coverage", "issue": "Model explicitly reported incomplete observation; independent complete playback is required for pass"})
        for field, content in advisory["coverage"].items():
            for match in re.finditer(r"(?<![\d:])(\d+):(\d{2})(?::(\d{2}))?(\.\d+)?", content):
                first, second, third, fraction = match.groups()
                seconds = int(first) * (3600 if third else 60) + int(second) * (60 if third else 1) + int(third or 0) + float(fraction or 0)
                if seconds > duration + 0.1:
                    warnings.append({"id": f"coverage-time-{len(warnings)+1}", "field": field,
                                     "text": match.group(), "parsed_seconds": seconds,
                                     "issue": "Model coverage timestamp exceeds probed duration; independently reconcile it"})
        issues = [{"id": f"model-blocker-{i+1}", "kind": "blocker", "advisory": finding}
                  for i, finding in enumerate(advisory["blockers"])]
        issues += [{"id": f"technical-{i+1}", "kind": "technical_candidate", "advisory": finding}
                   for i, finding in enumerate(technical.get("candidates", []))]
        issues += [{"id": f"model-advice-{i+1}", "kind": "optional_advice", "advisory": finding}
                   for i, finding in enumerate(advisory["advice"])]
        evidence = list({entry["path"]: entry for entry in artifacts}.values())
        return {"contract": CONTRACT, "id": identity, "aspect": aspect, "verdict": None,
                "agent_judgement": "pending", "publication_approval": False,
                **({"production_root_binding": ledger.digest_json(self.family_pin)} if self.family == "marketing" else {}),
                "source_fingerprint": self.global_source["fingerprint"], "input_sha256": variant["input_sha256"],
                "output": variant["output"], "job_path": job_path, "evidence": evidence,
                "checks": {check: "pending" for check in self.value["policy"]["required_checks"]},
                "lip_sync_selected": bool(review_item["context"]["approved_cta"].get("circle")),
                "technical": technical, "model_advisory": advisory, "issues": issues, "warnings": warnings,
                "coverage": {"duration_seconds": duration, "sampling": job.get("coverage"),
                             "note": "Model observed_complete and sampled frames are advisory, not independent playback or frame-level proof"}}


def evidence_for(root, entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError("Each judgement requires retained exact-byte evidence")
    if any(not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
           or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("sha256", ""))) for entry in entries):
        raise ValueError("Each judgement evidence entry requires its exact path and SHA-256")
    return [exact(root, entry["path"], entry["sha256"]) for entry in entries]


def decision_template(candidate, candidate_sha256):
    pending = lambda: {"status": "pending", "rationale": "", "evidence": []}
    return {"candidate_sha256": candidate_sha256, "verdict": None, "reviewer": "", "summary": "",
            "checks": {name: pending() for name in candidate["checks"]},
            "playback": {"output_sha256": candidate["output"]["sha256"],
                         "duration_seconds": candidate["coverage"]["duration_seconds"],
                         "start_seconds": None, "end_seconds": None, "complete_picture_and_original_audio": False,
                         "coverage_rationale": "", "model_timestamp_reconciliation": "", "evidence": []},
            "coverage_reconciliations": {warning["id"]: {"rationale": "", "evidence": []} for warning in candidate["warnings"]},
            "dispositions": {issue["id"]: pending() for issue in candidate["issues"]},
            "additional_findings": [], "repairs": []}


def final_payload(preparation, candidate_path, decision_path):
    root = preparation.root
    candidate_file, decision_file = exact(root, candidate_path), exact(root, decision_path)
    candidate = ledger.read(root / candidate_file["path"])
    decision = ledger.read(root / decision_file["path"])
    rebuilt = preparation.candidate(candidate["id"], candidate["aspect"], candidate["job_path"])
    if rebuilt != candidate or decision.get("candidate_sha256") != candidate_file["sha256"]:
        raise ValueError("Candidate, evidence or explicit decision binding changed; prepare and judge again")
    verdict = decision.get("verdict")
    if verdict not in {"pass", "needs_work", "blocked"} or not decision.get("reviewer", "").strip() \
            or not decision.get("summary", "").strip():
        raise ValueError("An explicit reviewer, verdict and summary are required")
    checks = decision.get("checks", {})
    if set(checks) != set(candidate["checks"]):
        raise ValueError("Judge every policy check separately")
    artifacts = candidate["evidence"] + [candidate_file, decision_file]
    statuses = {}
    for name, judgement in checks.items():
        status = judgement.get("status")
        if status not in {"pass", "needs_work", "uncertain", "not_applicable"} or not judgement.get("rationale", "").strip():
            raise ValueError("Pending or unsupported policy judgement: " + name)
        if status == "not_applicable" and (name != "selected_cta_lip_sync" or candidate["lip_sync_selected"]):
            raise ValueError("Selected CTA lip sync cannot be waived")
        artifacts += evidence_for(root, judgement.get("evidence"))
        statuses[name] = status
    playback = decision.get("playback", {})
    duration = candidate["coverage"]["duration_seconds"]
    if playback.get("output_sha256") != candidate["output"]["sha256"] \
            or type(playback.get("duration_seconds")) not in (int, float) \
            or not math.isfinite(playback["duration_seconds"]) \
            or abs(playback["duration_seconds"] - duration) > 0.1 \
            or type(playback.get("start_seconds")) not in (int, float) \
            or type(playback.get("end_seconds")) not in (int, float) \
            or not 0 <= playback["start_seconds"] <= playback["end_seconds"] <= duration + 0.1 \
            or not playback.get("coverage_rationale", "").strip() \
            or not playback.get("model_timestamp_reconciliation", "").strip():
        raise ValueError("Playback must bind exact bytes and reconcile model coverage against probed duration")
    artifacts += evidence_for(root, playback.get("evidence"))
    reconciliations = decision.get("coverage_reconciliations", {})
    if set(reconciliations) != {warning["id"] for warning in candidate["warnings"]}:
        raise ValueError("Explicitly reconcile every model coverage warning against the probed clip")
    for judgement in reconciliations.values():
        if not judgement.get("rationale", "").strip():
            raise ValueError("Coverage warning reconciliation needs a rationale")
        artifacts += evidence_for(root, judgement.get("evidence"))
    if verdict == "pass" and (playback.get("complete_picture_and_original_audio") is not True
            or playback["start_seconds"] > 0.1 or playback["end_seconds"] < duration - 0.1
            or statuses["full_video_playback"] != "pass" or candidate["technical"]["state"] != "technical_clear"
            or any(status not in {"pass", "not_applicable"} for status in statuses.values())):
        raise ValueError("A pass requires complete picture/sound playback, technical clearance and every explicit check")
    dispositions = decision.get("dispositions", {})
    if set(dispositions) != {issue["id"] for issue in candidate["issues"]}:
        raise ValueError("Explicitly reconcile every technical candidate, model blocker and optional suggestion")
    findings = []
    for issue in candidate["issues"]:
        judgement = dispositions[issue["id"]]
        status = judgement.get("status")
        allowed = {"fixed", "refuted", "open"} | ({"optional_not_required"} if issue["kind"] == "optional_advice" else set())
        if status not in allowed or not judgement.get("rationale", "").strip():
            raise ValueError("Every issue needs an explicit disposition and rationale")
        artifacts += evidence_for(root, judgement.get("evidence"))
        if status != "optional_not_required":
            findings.append({"id": issue["id"], "description": str(issue["advisory"]), "status": status,
                             "rationale": judgement["rationale"]})
        if verdict == "pass" and status == "open":
            raise ValueError("An unresolved issue cannot pass")
    additions = decision.get("additional_findings")
    repairs = decision.get("repairs")
    if not isinstance(additions, list) or not isinstance(repairs, list):
        raise ValueError("Explicit additional_findings and repairs lists are required")
    for finding in additions:
        if not finding.get("id") or not finding.get("description") or finding.get("status") not in {"open", "fixed", "refuted"}:
            raise ValueError("Invalid independently found issue")
        artifacts += evidence_for(root, finding.get("evidence"))
        if verdict == "pass" and finding["status"] == "open":
            raise ValueError("An independently found unresolved issue cannot pass")
    artifacts = list({entry["path"]: entry for entry in artifacts}.values())
    preparation.stable()
    return {"payload_contract": CONTRACT, "verdict": verdict, "reviewer": decision["reviewer"],
            "summary": decision["summary"], "checks": statuses, "findings": findings + additions,
            "repairs": repairs, "evidence": [entry["path"] for entry in artifacts],
            "expected_evidence": {entry["path"]: entry["sha256"] for entry in artifacts},
            "expected_input_sha256": candidate["input_sha256"],
            "expected_output_sha256": candidate["output"]["sha256"],
            "expected_source_fingerprint": candidate["source_fingerprint"],
            **({"expected_production_root_binding": candidate["production_root_binding"]}
               if "production_root_binding" in candidate else {}),
            "explicit_judgement": decision, "publication_approval": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "finalize"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ledger", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--ids", nargs="+")
    selection.add_argument("--kinds", nargs="+", choices=("cta", "marketing", "teaching_social_derivative"))
    parser.add_argument("--aspect", choices=("tall", "wide", "both"), default="both")
    parser.add_argument("--evidence-root", type=Path, default=Path("out/review-complete"))
    parser.add_argument("--output-dir", type=Path, default=Path("out/review-candidates"))
    parser.add_argument("--exact-request", type=Path, required=True)
    parser.add_argument("--exact-request-sha256", required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--decision", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        prepared = Preparation(root, args.ledger.expanduser())
        prepared.expected_request = args.exact_request
        prepared.expected_request_sha256 = args.exact_request_sha256
        if args.mode == "finalize":
            if not all((args.candidate, args.decision, args.output)):
                raise ValueError("finalize requires --candidate, --decision and --output")
            payload = final_payload(prepared, args.candidate, args.decision)
            atomic_json(root / relative(root, args.output), payload)
            print(json.dumps({"state": "explicit_payload_exported_not_recorded", "path": relative(root, args.output)}))
            return
        if args.ids != [media.policy.IDENTITY] or args.aspect != "wide" or args.kinds:
            raise ValueError("Isolated follow-up preparation requires exact compare-wide selection")
        if not args.ids and not args.kinds:
            raise ValueError("prepare requires explicit --ids or --kinds; no blanket approval")
        selected_ids = args.ids or [identity for identity, item in prepared.value["items"].items()
                                   if item.get("in_current_inventory") and item["kind"] in args.kinds]
        aspects = ledger.ASPECTS if args.aspect == "both" else (args.aspect,)
        evidence_root = root / relative(root, args.evidence_root)
        outdir = root / relative(root, args.output_dir)
        for identity in selected_ids:
            for aspect in aspects:
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", identity)
                matches = []
                rejected = []
                for path in [root / relative(root, args.exact_request)]:
                    try:
                        matches.append(prepared.candidate(identity, aspect, path))
                    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
                        rejected.append(f"{path.name}: {exc}")
                        continue
                if len(matches) != 1:
                    raise ValueError(f"{identity}/{aspect}: require exactly one intact current completed advisory, found {len(matches)}; "
                                     + "; ".join(rejected[:5]))
                candidate = matches[0]
                path = outdir / f"{safe}-{aspect}-{ledger.digest_json(candidate)[:20]}.candidate.json"
                if path.exists() and ledger.read(path) != candidate:
                    raise ValueError("Existing immutable candidate changed")
                if not path.exists():
                    atomic_json(path, candidate)
                template = path.with_name(path.name.replace(".candidate.json", ".decision-template.json"))
                if not template.exists():
                    atomic_json(template, decision_template(candidate, ledger.sha256(path)))
                print(json.dumps({"state": "judgement_pending", "candidate": relative(root, path),
                                  "candidate_sha256": ledger.sha256(path), "decision_template": relative(root, template),
                                  "warnings": candidate["warnings"]}), flush=True)
        prepared.stable()
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"prepare-review: {exc}\n")


if __name__ == "__main__":
    main()
