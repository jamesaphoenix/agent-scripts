#!/usr/bin/env python3
"""Complete-clip advisory review, with separate technical evidence and paid-call accounting.

No blanket approvals are written. Every model result still needs agent judgement.
Run `plan` anywhere. `technical`, `model`, and provider `cleanup` run on Studio only.

Examples (from founder-videos on Studio):
  python3 scripts/review-media.py plan --kinds cta --aspect both
  python3 scripts/review-media.py technical --ids cta-sc-01 --aspect tall
  python3 scripts/review-media.py model --ids cta-sc-01 --aspect tall \
    --ledger ~/render-tmp/smart-cub-refresh-20261016/ledger/media-review-ledger.json

Use --max-requests to bound each paid batch (default 1). All paid requests share the
existing authoritative ledger. Never copy a writable budget ledger to another host.
Pricing and API references were verified 2026-10-16:
https://ai.google.dev/gemini-api/docs/pricing.md
https://ai.google.dev/gemini-api/docs/generate-content/video-understanding
https://ai.google.dev/gemini-api/docs/generate-content/thinking.md
https://ai.google.dev/api/tokens
https://ai.google.dev/api/generate-content
https://ai.google.dev/api/files
"""
import argparse
import contextlib
import datetime
from decimal import Decimal, ROUND_CEILING
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SPEC = importlib.util.spec_from_file_location("media_review_ledger", Path(__file__).with_name("review-ledger.py"))
ledger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger)
ROOT = Path(__file__).resolve().parents[1]
MODEL = "gemini-3.6-flash"
MAX_OUTPUT = 4096
API = "https://generativelanguage.googleapis.com"
CHECKS = ("narration_and_captions", "layout_and_readability", "hook_and_pacing",
          "brand_and_claims", "cta_relevance_and_transition", "audio", "selected_cta_lip_sync")
TARIFF = {"model": MODEL, "service": "standard", "input_usd_per_million": "0.75",
          "cached_input_usd_per_million": "0.075", "output_including_thoughts_usd_per_million": "3.75",
          "verified_on": "2026-10-16", "valid_through": "2026-12-31",
          "source": "https://ai.google.dev/gemini-api/docs/pricing.md"}
SAFETY_INPUT = Decimal("1.50")
SAFETY_OUTPUT = Decimal("9.00")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
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
            raise ValueError("Another reviewer owns this item/aspect; do not duplicate its request") from exc
        yield


def inventory(root, published_manifest):
    scripts = ledger.read(root / "data/scripts.json")
    ctas = {row["id"]: row for row in ledger.read(root / "data/ctas.json")}
    assignments = ledger.read(root / "data/cta-assignments.json")
    teaching_path = root / "data/teaching-social-inputs.json"
    teaching = ledger.read(teaching_path)["videos"] if teaching_path.exists() else []
    published = {row["skillKey"]: row for row in ledger.read(published_manifest)["videos"]} if published_manifest.is_file() else {}
    result = {}
    for kind, source_rows in (("marketing", scripts), ("cta", list(ctas.values())), ("teaching", teaching)):
        for row in source_rows:
            identity = "teaching:" + row["skillKey"] if kind == "teaching" else row["id"]
            cta_id = identity if kind == "cta" else row.get("ctaId") if kind == "teaching" else assignments.get(identity)
            if cta_id not in ctas:
                raise ValueError(f"Missing or unknown CTA assignment: {identity}")
            body = "" if kind == "cta" else row.get("script")
            source = published.get(row.get("skillKey"), {})
            if kind == "teaching":
                body = "\n\n".join(source.get("transcript", [])) or None
            outputs = {aspect: row["media"][aspect]["derivativeAppRelativePath"] if kind == "teaching"
                       else f"out/{identity}-{aspect}.mp4" for aspect in ledger.ASPECTS}
            for path in outputs.values():
                ledger.portable(root, path)
            result[identity] = {"id": identity, "kind": kind, "outputs": outputs,
                "context": {"id": identity, "kind": kind, "audience": row.get("audience", "parents"),
                    "title": row.get("title", row.get("head", identity)),
                    "approved_body_script": body, "approved_cta": ctas[cta_id],
                    "source_row": row, "published_source": source if kind == "teaching" else None,
                    "original_teaching_sources_preserved": kind == "teaching",
                    "lip_sync_policy": "Selected CTA endings only; never marketing or teaching bodies"}}
    return result


def select(items, ids, kinds, aspect):
    unknown = set(ids or []) - set(items)
    if unknown:
        raise ValueError("Unknown video IDs: " + ", ".join(sorted(unknown)))
    aspects = ledger.ASPECTS if aspect == "both" else (aspect,)
    return [(item, value) for identity, item in items.items()
            if (not ids or identity in ids) and (not kinds or item["kind"] in kinds)
            for value in aspects]


def sampling_rate(context):
    # Inspect the short reusable lip-sync ending more closely in both aspects.
    # Its embedded copies still receive a complete picture-and-sound review.
    return 12 if context.get("kind") == "cta" and context.get("approved_cta", {}).get("circle") is True else 2


def original_make_prompt(context, aspect, technical_result):
    return """Review this COMPLETE finished Smart Cub Learning social video, including all speech,
all scenes, its transition and the complete selected CTA ending. Return concise JSON only.
The source video is uploaded in full with its original audio. Visual sampling is SAMPLING_RATEfps;
you cannot prove every intervening frame or exact phoneme alignment. Explicitly describe
uncertainty. Never imply exhaustive frame-level verification from the sampled frames.
Smart Cub Learning branding is expected and allowed, including its name, logo and URL.
Do not assume an avatar exists in the body. The intended policy is no avatar in marketing
or teaching bodies; lip sync is required only in the selected CTA ending if its `circle`
is true. A still portrait there is not completed lip sync. Circle=false requires no avatar.
Expressive directions such as [warmly] in the approved scripts are performance cues and
must not be spoken. Judge observed audio against the approved body AND approved CTA.
Teaching derivatives retain the complete approved teaching content and add a relevant CTA.
Flag time-sensitive claims for verification rather than inventing up-to-date facts.
Use short, direct British English and specific, actionable improvements. Report actual
observations with timestamps, not imagined problems. Distinguish confirmed defects from
suspicions. Do not flag large empty space by itself when the composition is intentional.
Black/freeze/silence technical candidates can be intentional and need judgement.
For tall video flag important text covered by right/bottom social UI, while recognising
different platforms have different safe zones. Check readable text, clipping, overlap,
contrast, captions, scene cues, hook, pacing, voice, audio joins and abrupt endings.
For each policy check report pass, needs_work, uncertain or not_applicable with evidence.
Only selected_cta_lip_sync may be not_applicable when the selected CTA has circle=false.
No overall publication approval: you provide advisory findings for a separate agent review.
The `coverage` must state the first and last observed content, what you heard in the CTA,
and limitations. List timestamped blockers and optional improvements separately. Bound
all timestamps by the supplied clip duration. If you could not observe the whole clip,
set observed_complete=false. `priority` is fix, needs_judgement or no_defects_observed.

CONTEXT (approved source and CTA definitions):
""".replace("SAMPLING_RATE", str(sampling_rate(context))) + json.dumps(context, ensure_ascii=False, sort_keys=True) + "\nASPECT: " + aspect + \
        "\nTECHNICAL EVIDENCE (independent, not a creative approval):\n" + json.dumps(technical_result, ensure_ascii=False, sort_keys=True)


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def result_schema():
    finding = {"type": "object", "properties": {
        "start_seconds": {"type": "number"}, "end_seconds": {"type": "number"},
        "category": {"type": "string"}, "issue": {"type": "string"},
        "evidence": {"type": "string"}, "repair": {"type": "string"}},
        "required": ["start_seconds", "end_seconds", "category", "issue", "evidence", "repair"]}
    check = {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["pass", "needs_work", "uncertain", "not_applicable"]},
        "evidence": {"type": "string"}}, "required": ["status", "evidence"]}
    return {"type": "object", "properties": {
        "observed_complete": {"type": "boolean"},
        "coverage": {"type": "object", "properties": {key: {"type": "string"} for key in
                     ("first_observed", "last_observed", "cta_speech_observed", "limitations")},
                     "required": ["first_observed", "last_observed", "cta_speech_observed", "limitations"]},
        "checks": {"type": "object", "properties": {key: check for key in CHECKS}, "required": list(CHECKS)},
        "blockers": {"type": "array", "items": finding}, "advice": {"type": "array", "items": finding},
        "priority": {"type": "string", "enum": ["fix", "needs_judgement", "no_defects_observed"]}},
        "required": ["observed_complete", "coverage", "checks", "blockers", "advice", "priority"]}


def example_result():
    return {"observed_complete": True, "coverage": {"first_observed": "Opening", "last_observed": "CTA",
        "cta_speech_observed": "Follow", "limitations": "2fps visual sampling"},
        "checks": {key: {"status": "pass", "evidence": "Observed in complete clip"} for key in CHECKS},
        "blockers": [], "advice": [], "priority": "no_defects_observed"}


def validate_result(value, duration):
    if not isinstance(value, dict) or set(value) != set(result_schema()["required"]):
        raise ValueError("Model response does not match required advisory structure")
    if type(value["observed_complete"]) is not bool or value["priority"] not in {"fix", "needs_judgement", "no_defects_observed"}:
        raise ValueError("Invalid coverage or priority")
    if not isinstance(value["checks"], dict) or set(value["checks"]) != set(CHECKS):
        raise ValueError("Missing model policy checks")
    for name, check in value["checks"].items():
        if not isinstance(check, dict) or check.get("status") not in {"pass", "needs_work", "uncertain", "not_applicable"} \
                or not isinstance(check.get("evidence"), str) or not check["evidence"].strip():
            raise ValueError("Missing check evidence")
        if check["status"] == "not_applicable" and name != "selected_cta_lip_sync":
            raise ValueError("Only selected_cta_lip_sync may be not_applicable")
    if not isinstance(value["coverage"], dict) or not all(isinstance(value["coverage"].get(key), str) and value["coverage"][key].strip()
               for key in ("first_observed", "last_observed", "cta_speech_observed", "limitations")):
        raise ValueError("Missing complete-clip coverage evidence")
    if not isinstance(value["blockers"], list) or not isinstance(value["advice"], list):
        raise ValueError("Model findings must be lists")
    for finding in value["blockers"] + value["advice"]:
        if not isinstance(finding, dict):
            raise ValueError("Model finding must be an object")
        start, end = finding.get("start_seconds"), finding.get("end_seconds")
        if type(start) not in (int, float) or type(end) not in (int, float) \
                or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end <= duration + 0.1:
            raise ValueError("Invalid finding timestamp")
        if not all(isinstance(finding.get(key), str) and finding[key].strip() for key in ("category", "issue", "evidence", "repair")):
            raise ValueError("Finding lacks evidence or actionable repair")



# Task-owned isolated follow-up. The canonical helper remains untouched.
POLICY_SPEC = importlib.util.spec_from_file_location("compare_wide_policy", Path(__file__).with_name("variant-policy.py"))
policy = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(policy)

def make_prompt(context, aspect, technical_result):
    policy.require_target(context["id"], aspect)
    return original_make_prompt(context, aspect, technical_result) + policy.APPENDIX

def request_body(uri, prompt, fps=2):
    return {"serviceTier": "standard", "contents": [{"role": "user", "parts": [
        {"fileData": {"mimeType": "video/mp4", "fileUri": uri}, "videoMetadata": {"fps": fps}, "mediaProcessing": "STATIC"},
        {"text": prompt}]}], "generationConfig": {
            "temperature": 0, "candidateCount": 1, "maxOutputTokens": MAX_OUTPUT,
            "thinkingConfig": {"thinkingLevel": "low"}, "mediaResolution": "MEDIA_RESOLUTION_MEDIUM",
            "responseMimeType": "application/json", "responseJsonSchema": result_schema()}}


def technical_candidates(log):
    findings = []
    for line in log.splitlines():
        kind = next((kind for kind, marker in (("black", "black_start:"), ("freeze", "freeze_"),
                     ("silence", "silence_")) if marker in line), None)
        if kind:
            findings.append({"kind": kind, "status": "needs_judgement", "ffmpeg_evidence": line.strip()})
    return findings


def technical(path, aspect, evidence_dir):
    ledger.require_studio()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=120)
    if probe.returncode:
        raise ValueError("ffprobe failed; inspect this media on Studio")
    metadata = json.loads(probe.stdout)
    atomic_json(evidence_dir / "ffprobe.json", metadata)
    streams = metadata.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float(metadata.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Missing positive clip duration")
    expected = (1080, 1920) if aspect == "tall" else (1920, 1080)
    dimensions_ok = bool(video and (video.get("width"), video.get("height")) == expected)
    command = ["ffmpeg", "-nostdin", "-v", "info", "-xerror", "-i", str(path),
               "-map", "0:v:0", "-map", "0:a:0", "-vf",
               "blackdetect=d=0.25:pix_th=0.10,freezedetect=n=-50dB:d=2", "-af",
               "silencedetect=noise=-50dB:d=1,astats=metadata=0:reset=0,ebur128=peak=true", "-f", "null", "-"]
    decoded = subprocess.run(command, capture_output=True, text=True, timeout=max(180, int(duration * 15))) if video and audio else None
    log = decoded.stderr if decoded else "Full decode not attempted: missing required video or audio stream"
    log_path = evidence_dir / "ffmpeg.log"
    log_path.write_text(log)
    result = {"state": "technical_clear" if decoded and decoded.returncode == 0 and dimensions_ok else "technical_failed",
              "scope": "Full video+audio decode, metadata, black/freeze/silence detection, audio stats and true-peak/loudness analysis",
              "creative_approval": False, "duration_seconds": duration, "dimensions_ok": dimensions_ok,
              "video_present": bool(video), "audio_present": bool(audio),
              "full_decode_ok": bool(decoded and decoded.returncode == 0),
              "candidates": technical_candidates(log),
              "audio_measurements": [line.strip() for line in log.splitlines() if any(marker in line for marker in
                 ("Peak level dB", "RMS level dB", "Number of NaNs", "Number of Infs", "Integrated loudness:", "True peak:", "I:", "Peak:"))][-35:],
              "ffprobe_sha256": ledger.sha256(evidence_dir / "ffprobe.json"), "ffmpeg_log_sha256": ledger.sha256(log_path),
              "machine": socket.gethostname(), "checked_at": ledger.now()}
    atomic_json(evidence_dir / "technical.json", result)
    return result


def preflight(count):
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("countTokens did not return a positive token count")
    # Tokeniser/generation metadata can differ slightly. No tools or grounding are enabled.
    bound = count + math.ceil(count / 10) + 1024
    if bound + MAX_OUTPUT > 1048576:
        raise ValueError("Preflight would exceed the supported context bound")
    usd = (Decimal(bound) * SAFETY_INPUT + Decimal(MAX_OUTPUT) * SAFETY_OUTPUT) / 1000000
    return {"counted_input_tokens": count, "maximum_input_tokens": bound, "maximum_output_including_thoughts": MAX_OUTPUT,
            "maximum_cents": int((usd * 100).to_integral_value(rounding=ROUND_CEILING)),
            "reservation_input_usd_per_million": str(SAFETY_INPUT), "reservation_output_usd_per_million": str(SAFETY_OUTPUT)}


def usage_cost(usage):
    required = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")
    if not all(isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool) and usage[key] >= 0 for key in required):
        raise ValueError("Missing or invalid usage receipt; retain reservation")
    thoughts = usage.get("thoughtsTokenCount", 0)
    cached = usage.get("cachedContentTokenCount", 0)
    tools = usage.get("toolUsePromptTokenCount", 0)
    if not all(isinstance(number, int) and not isinstance(number, bool) and number >= 0 for number in (thoughts, cached, tools)):
        raise ValueError("Invalid usage token counts; retain reservation")
    output = usage["candidatesTokenCount"] + thoughts
    if cached > usage["promptTokenCount"] or tools or usage["totalTokenCount"] != usage["promptTokenCount"] + output:
        raise ValueError("Inconsistent usage receipt or unexpected tool usage; retain reservation")
    usd = (Decimal(usage["promptTokenCount"] - cached) * Decimal(TARIFF["input_usd_per_million"])
           + Decimal(cached) * Decimal(TARIFF["cached_input_usd_per_million"])
           + Decimal(output) * Decimal(TARIFF["output_including_thoughts_usd_per_million"])) / 1000000
    return {"tariff": TARIFF, "generated_tokens_including_thoughts": output,
            "tariff_calculated_usd": str(usd), "ledger_cents_rounded_up": int((usd * 100).to_integral_value(rounding=ROUND_CEILING)),
            "provider_invoice_confirmed": False,
            "basis": "Usage-token receipt multiplied by verified standard tariff; estimated bill, rounded up in shared budget"}


class ProviderError(ValueError):
    pass


class Gemini:
    def __init__(self):
        # op reads at runtime only; never put the key into args, URLs, files or logs.
        if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
            raise ProviderError("Source the existing shell profile to load OP_SERVICE_ACCOUNT_TOKEN before running")
        secret = subprocess.run(["op", "read", "op://api-keys/Gemini/api_key"], capture_output=True, text=True)
        if secret.returncode or not secret.stdout.strip():
            raise ProviderError("1Password Gemini key read failed; no paid request submitted")
        self._key = secret.stdout.strip()

    def request(self, endpoint, body=None, method=None, headers=None, on_headers=None, raw=False):
        url = endpoint if endpoint.startswith("https://") else API + endpoint
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "generativelanguage.googleapis.com":
            raise ProviderError("Refusing unexpected provider upload host")
        data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        safe_headers = {"x-goog-api-key": self._key, **({"Content-Type": "application/json"} if body is not None and not raw else {}), **(headers or {})}
        request = urllib.request.Request(url, data=data, method=method, headers=safe_headers)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                # Persist server IDs as soon as headers arrive, before waiting for body bytes.
                identity = next((response.headers.get(key) for key in ("x-request-id", "x-goog-request-id", "x-google-request-id") if response.headers.get(key)), None)
                if on_headers:
                    on_headers(identity)
                payload = response.read()
                return (json.loads(payload) if payload else {}), response.headers
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"Gemini HTTP {exc.code}; no automatic retry; inspect saved request state") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"Gemini transport failed ({type(exc).__name__}); no automatic retry; retain any reservation") from None

    def model(self):
        endpoint = "/v1beta/models?pageSize=1000"
        while endpoint:
            data, _ = self.request(endpoint)
            model = next((item for item in data.get("models", []) if item["name"] == "models/" + MODEL), None)
            if model:
                if "generateContent" not in model.get("supportedGenerationMethods", []):
                    raise ProviderError("Selected model does not expose generateContent; do not substitute silently")
                return {key: model.get(key) for key in ("name", "version", "inputTokenLimit", "outputTokenLimit", "supportedGenerationMethods")}
            token = data.get("nextPageToken")
            endpoint = "/v1beta/models?pageSize=1000&pageToken=" + urllib.parse.quote(token, safe="") if token else None
        raise ProviderError("Selected model not available to this key; do not substitute silently")

    def upload(self, path, save_file):
        _, headers = self.request("/upload/v1beta/files", {"file": {"displayName": path.name}}, headers={
            "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size), "X-Goog-Upload-Header-Content-Type": "video/mp4"})
        url = headers.get("X-Goog-Upload-URL")
        if not url:
            raise ProviderError("No resumable upload URL returned")
        data, _ = self.request(url, path.read_bytes(), headers={"X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize", "Content-Type": "video/mp4"}, raw=True)
        provider_file = data["file"]
        save_file(provider_file)  # durable provider file ID before any polling wait
        deadline = time.monotonic() + 600
        while provider_file.get("state") == "PROCESSING":
            if time.monotonic() >= deadline:
                raise ProviderError("Provider file processing timed out")
            time.sleep(3)
            provider_file, _ = self.request("/v1beta/" + provider_file["name"])
        if provider_file.get("state") != "ACTIVE":
            raise ProviderError("Provider upload did not become ACTIVE")
        return provider_file

    def delete(self, name):
        if not re.fullmatch(r"files/[A-Za-z0-9_-]+", name):
            raise ProviderError("Invalid provider file identifier")
        try:
            self.request("/v1beta/" + name, method="DELETE")
        except ProviderError as exc:
            if "HTTP 404" not in str(exc):
                raise


def current_review(job, version_key):
    if job.get("state") != "completed_advisory" or job.get("version_key") != version_key:
        return False
    artifacts = [(job.get(path), job.get(checksum)) for path, checksum in (
        ("response_path", "response_sha256"), ("technical_evidence", "technical_evidence_sha256"),
        ("context_path", "context_file_sha256"), ("prompt_path", "prompt_file_sha256"),
        ("request_body_path", "request_body_file_sha256"))]
    artifacts += [(item.get("path"), item.get("sha256")) for item in job.get("technical_artifacts", [])]
    return bool(len(job.get("technical_artifacts", [])) == 2 and all(
        filename and checksum and Path(filename).is_file() and ledger.sha256(Path(filename)) == checksum
        for filename, checksum in artifacts))


def retry_decision(job):
    if job.get("state") == "completed_advisory":
        return False
    if job.get("reservation_id"):
        raise ValueError("Prior paid submission is uncertain or unresolved; reconcile its saved job and reservation before another call")
    return True


def technical_summary(value):
    return {key: value[key] for key in ("state", "duration_seconds", "dimensions_ok", "video_present", "audio_present",
                                       "full_decode_ok", "candidates", "audio_measurements")}


def technical_current(value, directory, implementation, output_hash):
    return bool(value and value.get("implementation_sha256") == implementation and value.get("output_sha256") == output_hash
                and all((directory / name).is_file() and ledger.sha256(directory / name) == value.get(field)
                        for name, field in (("ffprobe.json", "ffprobe_sha256"), ("ffmpeg.log", "ffmpeg_log_sha256"))))


def review_one(root, ledger_path, item, aspect, evidence_root, client, model_info, technical_only=False):
    ledger.require_studio()
    relative = item["outputs"][aspect]
    media = ledger.file_snapshot(root, relative, True)
    if not media["sha256"]:
        return {"id": item["id"], "aspect": aspect, "state": "missing_output", "path": relative, "paid_requests": 0}
    policy.require_target(item["id"], aspect, media["sha256"])
    directory = evidence_root / re.sub(r"[^A-Za-z0-9_.-]", "_", item["id"]) / aspect / media["sha256"][:20]
    implementation = ledger.sha256(Path(__file__))
    with exclusive(directory / ".review.lock"):
        technical_path = directory / "technical.json"
        evidence = ledger.read(technical_path) if technical_path.is_file() else None
        if not technical_current(evidence, directory, implementation, media["sha256"]):
            evidence = technical(root / relative, aspect, directory)
            evidence.update({"implementation_sha256": implementation, "output_sha256": media["sha256"]})
            atomic_json(technical_path, evidence)
        if technical_only or evidence["state"] == "technical_failed":
            return {"id": item["id"], "aspect": aspect, "state": evidence["state"], "paid_requests": 0,
                    "technical_evidence": str(technical_path), "agent_judgement": "pending"}
        if item["context"]["approved_body_script"] is None:
            raise ValueError("Missing approved teaching transcript; supply --published-manifest before a paid review")
        prompt = make_prompt(item["context"], aspect, technical_summary(evidence))
        fps = sampling_rate(item["context"])
        body_template = request_body("FULL_CLIP_URI_REPLACED_AFTER_UPLOAD", prompt, fps)
        source_ledger = ledger.read(ledger_path)
        source_item = source_ledger.get("items", {}).get(item["id"], {}).get("aspects", {}).get(aspect, {})
        observation_identity = {"output_sha256": media["sha256"], "context_sha256": ledger.digest_json(item["context"]),
                   "prompt_sha256": text_hash(prompt), "request_template_sha256": ledger.digest_json(body_template),
                   "implementation_sha256": implementation, "model": MODEL, "model_metadata": model_info}
        version = {**observation_identity,
                   "ledger_input_sha256_at_start": source_item.get("input_sha256"),
                   "ledger_source_fingerprint_at_start": (source_ledger.get("source_snapshot") or {}).get("fingerprint"),
                   "render_source_freshness": "Not proved by model review; final ledger inventory and exact-version judgement still required"}
        # Model observations bind the actual bytes and approved context. An unrelated
        # source repair must reopen final ledger approval, but cannot change what was
        # observed in an identical clip or justify charging for that observation again.
        key = ledger.digest_json(observation_identity)
        job_path = directory / ("request-" + key[:24] + ".json")
        job = ledger.read(job_path) if job_path.is_file() else None
        if job and current_review(job, key):
            return {"id": item["id"], "aspect": aspect, "state": "skipped_exact_version_advisory", "paid_requests": 0,
                    "evidence": str(job_path), "agent_judgement": "pending"}
        for other_path in directory.glob("request-*.json"):
            other = ledger.read(other_path)
            retry_decision(other)
        if job and job.get("state") == "completed_advisory":
            raise ValueError("Completed review evidence changed or is missing; reconcile it instead of automatically paying again")
        job = {"schema_version": 1, "state": "prepared", "id": item["id"], "aspect": aspect,
               "version_key": key, "version": version, "source_media": media,
               "created_at": ledger.now(), "machine": socket.gethostname(), "technical_evidence": str(technical_path),
               "technical_evidence_sha256": ledger.sha256(technical_path), "model_review": "pending", "agent_judgement": "pending",
               "technical_artifacts": [{"path": str(directory / name), "sha256": ledger.sha256(directory / name)}
                                       for name in ("ffprobe.json", "ffmpeg.log")],
               "publication_approval": False, "coverage": {"entire_clip_uploaded": True, "audio": "original complete audio track",
                   "visual_sampling_fps": fps, "frame_level_verification": False}}
        context_path = directory / ("context-" + key[:24] + ".json")
        prompt_path = directory / ("prompt-" + key[:24] + ".txt")
        atomic_json(context_path, item["context"])
        prompt_path.write_text(prompt, encoding="utf-8")
        job.update({"context_path": str(context_path), "context_file_sha256": ledger.sha256(context_path),
                    "prompt_path": str(prompt_path), "prompt_file_sha256": ledger.sha256(prompt_path)})
        atomic_json(job_path, job)

        def save_file(provider_file):
            job["provider_file"] = {key: provider_file.get(key) for key in ("name", "uri", "state", "expirationTime")}
            job["state"] = "uploaded"
            atomic_json(job_path, job)

        try:
            provider_file = client.upload(root / relative, save_file)
            body = request_body(provider_file["uri"], prompt, fps)
            request_path = directory / ("body-" + key[:24] + ".json")
            atomic_json(request_path, body)
            job.update({"request_body_path": str(request_path), "request_body_file_sha256": ledger.sha256(request_path),
                        "request_body_sha256": ledger.digest_json(body)})
            atomic_json(job_path, job)
            count, _ = client.request("/v1beta/models/" + MODEL + ":countTokens",
                                      {"generateContentRequest": {"model": "models/" + MODEL, **body}})
            bound = preflight(count.get("totalTokens"))
            job.update({"preflight": bound, "count_tokens_receipt": count, "state": "counted"})
            atomic_json(job_path, job)
            if ledger.file_snapshot(root, relative, True) != media:
                raise ValueError("Output changed during provider preprocessing; no paid request submitted")
            policy.require_bound(bound)
            reservation_id = "review:" + key[:40]
            # Write intent before reservation. If interrupted in either transaction window,
            # the next invocation refuses automatic resubmission and retains the budget.
            job.update({"reservation_id": reservation_id, "state": "reserving"})
            atomic_json(job_path, job)
            try:
                ledger.reserve(ledger_path, reservation_id, bound["maximum_cents"], "review",
                               f"Complete {item['id']} {aspect}, {MODEL}, maxOutput4096 including thoughts; {key}")
            except ValueError:
                # A validation/cap refusal happens before the atomic reservation write.
                # No paid request was attempted; a later bounded run may try again.
                job.update({"reservation_intent_id": job.pop("reservation_id"), "state": "reservation_not_created"})
                atomic_json(job_path, job)
                raise
            job.update({"state": "submitted_unknown", "client_submission_id": str(uuid.uuid4()),
                        "submitted_at": ledger.now(), "provider_request_id": None,
                        "provider_id_note": "Synchronous generateContent has no server ID before response headers; client intent persisted before send"})
            atomic_json(job_path, job)

            def save_headers(provider_id):
                job.update({"provider_request_id": provider_id, "response_headers_at": ledger.now()})
                atomic_json(job_path, job)

            response, _ = client.request("/v1beta/models/" + MODEL + ":generateContent", body, on_headers=save_headers)
            response_path = directory / ("response-" + key[:24] + ".json")
            atomic_json(response_path, response)
            job.update({"state": "response_received", "provider_response_id": response.get("responseId"),
                        "returned_model_version": response.get("modelVersion"), "usage_receipt": response.get("usageMetadata"),
                        "response_path": str(response_path), "response_sha256": ledger.sha256(response_path)})
            atomic_json(job_path, job)
            actual_version = response.get("modelVersion", "")
            if not actual_version.startswith(MODEL):
                raise ValueError("Unexpected returned model version; retain reservation until pricing is reconciled")
            usage = response.get("usageMetadata", {})
            if str(usage.get("serviceTier", "standard")).lower() not in {"standard", "unspecified", "service_tier_unspecified"}:
                raise ValueError("Unexpected provider service tier; retain reservation")
            cost = usage_cost(usage)
            if usage["promptTokenCount"] > bound["maximum_input_tokens"] or cost["generated_tokens_including_thoughts"] > MAX_OUTPUT:
                raise ValueError("Usage exceeded the preflight token bounds; retain reservation and reconcile")
            job["usage_cost_estimate"] = cost
            atomic_json(job_path, job)
            ledger.settle(ledger_path, reservation_id, cost["ledger_cents_rounded_up"],
                f"Usage-tariff estimate, not invoice: {response.get('responseId') or key}; USD{cost['tariff_calculated_usd']}; {TARIFF['source']}")
            job["budget_state"] = "settled_usage_tariff_estimate_rounded_up"
            atomic_json(job_path, job)
            candidates = response.get("candidates", [])
            if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
                raise ValueError("Model output was blocked or truncated; retained paid receipt needs judgement, no automatic retry")
            text = "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", []) if not part.get("thought"))
            result = json.loads(text)
            validate_result(result, evidence["duration_seconds"])
            job.update({"state": "completed_advisory", "model_review": "completed_full_clip_observation" if result["observed_complete"] else "incomplete_observation", "advisory": result,
                        "completed_at": ledger.now(), "agent_judgement": "pending", "publication_approval": False})
            if ledger.file_snapshot(root, relative, True) != media:
                job.update({"state": "reopened_output_changed", "model_review": "stale"})
            atomic_json(job_path, job)
            return {"id": item["id"], "aspect": aspect, "state": job["state"], "paid_requests": 1,
                    "evidence": str(job_path), "cost_estimate": cost, "agent_judgement": "pending"}
        except BaseException as exc:
            job["last_error"] = {"type": type(exc).__name__, "message": str(exc)[:350], "at": ledger.now()}
            atomic_json(job_path, job)
            raise
        finally:
            name = job.get("provider_file", {}).get("name")
            if name:
                try:
                    client.delete(name)
                    job["provider_file_cleanup"] = {"state": "deleted", "at": ledger.now()}
                except Exception as exc:
                    job["provider_file_cleanup"] = {"state": "pending", "error_type": type(exc).__name__, "at": ledger.now()}
                atomic_json(job_path, job)


def tariff_current():
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if not TARIFF["verified_on"] <= today <= TARIFF["valid_through"]:
        raise ValueError("Pinned pricing verification is outside its effective dates; verify official pricing before paid calls")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("plan", "technical", "model", "cleanup"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ledger", type=Path, help="Required for paid mode: the one authoritative shared budget JSON")
    parser.add_argument("--published-manifest", type=Path, help="Approved teaching transcripts; defaults to repository published.json")
    parser.add_argument("--evidence-root", type=Path, default=Path("out/review-complete"))
    choose = parser.add_mutually_exclusive_group(required=True)
    choose.add_argument("--ids", nargs="+", help="Exact IDs (teaching:<skillKey> for teaching)")
    choose.add_argument("--kinds", nargs="+", choices=("marketing", "cta", "teaching"))
    choose.add_argument("--all", action="store_true")
    parser.add_argument("--aspect", choices=("tall", "wide", "both"), default="both")
    parser.add_argument("--max-requests", type=int, default=1, help="Maximum paid model requests this invocation; default 1")
    args = parser.parse_args()
    try:
        policy.require_args(args)
        policy.require_terminal_origin()
        root = args.root.resolve()
        published = args.published_manifest.resolve() if args.published_manifest else (root / "../../web/content/skill-videos/published.json").resolve()
        items = inventory(root, published)
        targets = select(items, args.ids, args.kinds, args.aspect)
        if args.mode == "plan":
            print(json.dumps({"state": "plan_only_no_media_reads_or_paid_calls", "count": len(targets),
                "outputs": [{"id": item["id"], "kind": item["kind"], "aspect": aspect, "path": item["outputs"][aspect],
                             "exists": (root / item["outputs"][aspect]).is_file(),
                             "approved_script_available": item["context"]["approved_body_script"] is not None} for item, aspect in targets]}, indent=2))
            return
        ledger.require_studio()
        evidence_root = args.evidence_root if args.evidence_root.is_absolute() else root / args.evidence_root
        client, model_info = None, None
        if args.mode in {"model", "cleanup"}:
            client = Gemini()
        if args.mode == "model":
            tariff_current()
            if args.ledger is None or not args.ledger.expanduser().is_file():
                raise ValueError("Paid mode requires an existing authoritative --ledger")
            if args.max_requests <= 0:
                raise ValueError("--max-requests must be positive")
            model_info = client.model()  # free models.list: no substitution if unavailable
        requests = 0
        for item, aspect in targets:
            if args.mode == "cleanup":
                target_dir = evidence_root / re.sub(r"[^A-Za-z0-9_.-]", "_", item["id"]) / aspect
                for job_path in target_dir.glob("*/request-*.json"):
                    with exclusive(job_path.parent / ".review.lock"):
                        job = ledger.read(job_path)
                        name = job.get("provider_file", {}).get("name")
                        if name and job.get("provider_file_cleanup", {}).get("state") != "deleted":
                            client.delete(name)
                            job["provider_file_cleanup"] = {"state": "deleted", "at": ledger.now()}
                            atomic_json(job_path, job)
                            print(json.dumps({"state": "provider_file_deleted", "id": item["id"], "aspect": aspect}))
                continue
            if args.mode == "model" and requests >= args.max_requests:
                print(json.dumps({"state": "batch_request_limit_reached", "paid_requests": requests}))
                break
            result = review_one(root, args.ledger.expanduser() if args.ledger else None, item, aspect,
                                evidence_root, client, model_info, technical_only=args.mode == "technical")
            requests += result["paid_requests"]
            print(json.dumps(result), flush=True)
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.exit(1, f"review-media: {exc}\n")


if __name__ == "__main__":
    main()
