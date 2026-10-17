#!/usr/bin/env python3
"""Durable, budgeted Fal lip sync for selected CTA endings. Plan-only unless --execute.

Uses one queue POST per input identity. Ambiguous submission intents never auto-retry.
All media reads, ffprobe, secret reads, downloads and API calls require Mac Studio.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import fcntl
import hashlib
import importlib.util
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

APP = Path(__file__).resolve().parent.parent
RUN_ROOT = Path.home() / "render-tmp/smart-cub-refresh-20261016"
LEDGER = RUN_ROOT / "ledger/media-review-ledger.json"
STATES = RUN_ROOT / "avatar-jobs"
ENDPOINT = "veed/fabric-1.0"
SUBMIT_URL = "https://queue.fal.run/" + ENDPOINT
INPUT_TRANSPORT_VERSION = "fal-cdn-upload-v1"
FAL_CLIENT_VERSION = "1.0.3"
RATE_CENTS = Decimal("15")
MAX_OUTPUT_SECONDS = 20
SELECTED = {f"cta-sc-{number:02d}" for number in range(1, 6)}
KEY_LOCK = threading.Lock()
KEY: str | None = None


def now():
    return datetime.now(timezone.utc).isoformat()


def require_studio(hostname=None):
    host = socket.gethostname() if hostname is None else hostname
    if re.sub(r"[^a-z0-9]", "", host.split(".")[0].lower()) != "jamessmacstudio":
        raise ValueError(f"Media and API work is restricted to Mac Studio; current host: {host}")


def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def lease(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"Another avatar process holds {path}")
        try:
            stream.seek(0)
            stream.truncate()
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "startedAt": now()}, stream)
            stream.flush()
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def selected_ids(rows, requested):
    allowed = {row["id"] for row in rows if row.get("circle") is True and row["id"] in SELECTED}
    ids = list(dict.fromkeys(requested or sorted(allowed)))
    if not ids or any(identity not in allowed for identity in ids):
        raise ValueError("Only circle-enabled CTA endings cta-sc-01 through cta-sc-05 may be lip-synced")
    return ids


def reserve_cents(duration):
    if not math.isfinite(duration) or duration <= 0 or duration + 4 > MAX_OUTPUT_SECONDS:
        raise ValueError("CTA audio must be positive and at most 16 seconds (20-second output allowance)")
    return int(((Decimal(str(duration)) + 4) * RATE_CENTS).to_integral_value(rounding=ROUND_CEILING))


def safe_url(url, kind="media", request_id=None):
    if not isinstance(url, str):
        raise ValueError("Missing Fal URL")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443)):
        raise ValueError("Unsafe Fal URL")
    if kind == "queue":
        if host != "queue.fal.run" or not parsed.path.startswith("/veed/fabric-1.0/"):
            raise ValueError("Unexpected Fal queue URL")
        prefix = f"/veed/fabric-1.0/requests/{request_id}"
        if not request_id or parsed.path not in (prefix, prefix + "/status", prefix + "/response"):
            raise ValueError("Fal queue URL does not match its request ID")
    elif host != "fal.media" and not host.endswith(".fal.media"):
        raise ValueError("Download URL must belong to Fal's HTTPS media CDN")
    return url


def input_url(url):
    safe_url(url)
    if len(url) > 2083:
        raise ValueError("Fal input URL exceeds the provider's 2083-character bound")
    return url


def upload_file(path):
    """Official SDK storage only. Paid inference retains the separate single-POST client."""
    require_studio()
    if importlib.metadata.version("fal-client") != FAL_CLIENT_VERSION:
        raise ValueError(f"Install pinned fal-client=={FAL_CLIENT_VERSION} in the Studio virtualenv")
    import fal_client
    try:
        return fal_client.SyncClient(key=fal_key()).upload_file(path)
    except Exception:
        # SDK exception text can include signed upload URLs or request headers.
        raise ValueError("Fal storage upload failed; no paid submission started; saved uploads retained") from None


def prepare_uploads(path, state, portrait, audio, uploader=None):
    require_studio()
    uploader = uploader or upload_file
    uploads = state.setdefault("inputUploads", {})
    for field, source, hash_key, content_type in [
        ("image_url", portrait, "portraitSha256", "image/jpeg"),
        ("audio_url", audio, "audioSha256", "audio/mpeg"),
    ]:
        expected = state["inputs"][hash_key]
        if checksum(source) != expected:
            raise ValueError("Input media changed before upload or submission")
        if field in uploads:
            receipt = uploads[field]
            if (receipt.get("sha256") != expected or receipt.get("bytes") != source.stat().st_size
                    or receipt.get("transportVersion") != INPUT_TRANSPORT_VERSION):
                raise ValueError("Saved upload does not match the current input source")
            input_url(receipt["url"])
            continue
        if state["phase"] != "prepared":
            raise ValueError("An existing paid request is missing its saved input upload; reconcile it")
        url = input_url(uploader(source))
        if checksum(source) != expected:
            raise ValueError("Input media changed during upload; no paid submission started")
        uploads[field] = {"url": url, "path": str(source), "sha256": expected,
            "bytes": source.stat().st_size, "contentType": content_type,
            "transportVersion": INPUT_TRANSPORT_VERSION, "sdkVersion": FAL_CLIENT_VERSION,
            "uploadedAt": now()}
        atomic_json(path, state)  # Each completed upload survives interruption of the next one.
    return {**{field: input_url(uploads[field]["url"]) for field in ("image_url", "audio_url")},
            "resolution": "720p"}


def terminal_validation_proven(old):
    """Only explicitly reconciled, receipt-proven URL validation failures permit a replacement."""
    failure = old.get("terminal_failure", {})
    if (old.get("phase") != "provider-validation-failed" or old.get("output")
            or old.get("lastStatus") != "COMPLETED" or not old.get("request", {}).get("request_id")
            or failure.get("http_status") != 422 or failure.get("provider_queue_status") != "COMPLETED"):
        raise ValueError("Earlier request is not a proven terminal input-validation failure; reconcile first")
    receipt = Path(failure.get("receipt", ""))
    if (not receipt.is_file() or receipt.stat().st_size > 2_000_000
            or checksum(receipt) != failure.get("receipt_sha256")):
        raise ValueError("Terminal failure receipt is missing or its SHA256 does not match")
    body = json.loads(receipt.read_text())
    body = body.get("body", body)
    details = body.get("detail", []) if isinstance(body, dict) else []
    if (not details or any(not isinstance(row, dict) or row.get("type") != "url_too_long"
            or row.get("loc") not in (["body", "image_url"], ["body", "audio_url"]) for row in details)):
        raise ValueError("Terminal receipt does not prove the bounded input URL validation failure")
    return True


def validate_replacements(state):
    for replacement in state.get("replacesTerminalValidationFailures", []):
        previous = Path(replacement["path"])
        if not previous.is_file() or checksum(previous) != replacement["sha256"]:
            raise ValueError("Earlier reconciled request changed; recheck replacement proof before submission")
        terminal_validation_proven(json.loads(previous.read_text()))


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Fal HTTP redirects are refused; reconcile the saved request")


def fal_key():
    require_studio()
    global KEY
    with KEY_LOCK:
        if KEY is None:
            environment = os.environ.copy()
            if not environment.get("OP_SERVICE_ACCOUNT_TOKEN"):
                token = subprocess.run(["security", "find-generic-password", "-s",
                                        "op_service_account_token_cli_automation", "-w"],
                                       capture_output=True, text=True)
                if token.returncode or not token.stdout.strip():
                    raise ValueError("1Password service-account token is unavailable")
                environment["OP_SERVICE_ACCOUNT_TOKEN"] = token.stdout.strip()
            result = subprocess.run(["op", "read", "op://api-keys/Fal/api_key"],
                                    env=environment, capture_output=True, text=True)
            if result.returncode or not result.stdout.strip():
                raise ValueError("1Password Fal key read failed; check the service-account token")
            KEY = result.stdout.strip()
        return KEY


class FalHTTPError(ValueError):
    def __init__(self, code, receipt=None):
        self.code, self.receipt = code, receipt
        super().__init__(f"Fal HTTP {code}; error receipt, request state and reservation retained")


def api_json(url, body=None, error_path=None):
    require_studio()
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Key " + fal_key(), "Content-Type": "application/json",
                 "X-Fal-No-Retry": "1", "X-Fal-Queue-Priority": "low"},
        method="POST" if body is not None else "GET")
    # No client POST retries and no redirect that could repeat a submission or leak credentials.
    try:
        with urllib.request.build_opener(NoRedirects()).open(request, timeout=90) as response:
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise ValueError("Unexpectedly large Fal JSON response")
            return json.loads(content)
    except urllib.error.HTTPError as error:
        content = error.read(2_000_001)
        truncated = len(content) > 2_000_000
        content = content[:2_000_000]
        try:
            provider_body = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            provider_body = {"unparsedText": content.decode("utf-8", errors="replace")}
        receipt = None
        if error_path is not None:
            atomic_json(error_path, {"version": 1, "httpStatus": error.code, "url": url,
                "receivedAt": now(), "body": provider_body, "bodyTruncated": truncated,
                "bodySha256": hashlib.sha256(content).hexdigest()})
            os.chmod(error_path, 0o600)
            receipt = {"path": str(error_path), "sha256": checksum(error_path),
                       "httpStatus": error.code}
        raise FalHTTPError(error.code, receipt) from None


def saved_api_json(path, state, url, body=None):
    error_path = path.parent / f"{path.stem}.http-error-{uuid.uuid4().hex}.receipt"
    try:
        return api_json(url, body, error_path)
    except FalHTTPError as error:
        if error.receipt:
            state.setdefault("httpErrors", []).append(error.receipt)
            atomic_json(path, state)
        raise


def submit_once(path, state, submit):
    if state.get("request"):
        return state  # Never POST again after a request ID was persisted.
    if state["phase"] != "prepared":
        raise ValueError("Submission outcome is uncertain. Reconcile the saved intent; do not retry the paid POST")
    state.update({"phase": "submission-intent", "submissionIntentAt": now()})
    atomic_json(path, state)  # Must reach durable storage before the network operation.
    response = submit()
    state.update({"phase": "queued", "request": {key: response.get(key) for key in
                  ["request_id", "status_url", "response_url"]}, "submittedAt": now()})
    atomic_json(path, state)  # Persist all URLs and ID before validation or polling.
    return state


def probe(path):
    require_studio()
    return json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
                                              "-show_format", "-of", "json", str(path)], text=True))


def duration(metadata, stream):
    values = [float(value) for value in [stream.get("duration"), metadata.get("format", {}).get("duration")]
              if value not in (None, "N/A")]
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Missing or invalid media duration")
    return max(values)


def validate_output(metadata, audio_seconds):
    videos = [stream for stream in metadata["streams"] if stream["codec_type"] == "video"]
    audios = [stream for stream in metadata["streams"] if stream["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError("Avatar output needs exactly one picture and audio stream")
    # Inspect each stream's own duration, using the container only when unavailable.
    actual = {}
    for kind, stream in [("video", videos[0]), ("audio", audios[0])]:
        value = stream.get("duration")
        seconds = float(value) if value not in (None, "N/A") else duration(metadata, stream)
        if (not math.isfinite(seconds) or seconds < audio_seconds - 0.08
                or seconds > min(audio_seconds + 4, MAX_OUTPUT_SECONDS) + 0.08):
            raise ValueError(f"Avatar {kind} duration does not preserve the full CTA audio within its allowance")
        actual[kind + "Seconds"] = seconds
    container_seconds = float(metadata.get("format", {}).get("duration", "nan"))
    if (not math.isfinite(container_seconds)
            or container_seconds > min(audio_seconds + 4, MAX_OUTPUT_SECONDS) + 0.08
            or container_seconds < max(actual.values()) - 0.08):
        raise ValueError("Avatar container duration falls outside its bounded streams")
    if min(videos[0]["width"], videos[0]["height"]) < 700:
        raise ValueError("Avatar output is below the requested 720p resolution")
    return {**actual, "width": videos[0]["width"], "height": videos[0]["height"]}


def download(url, output):
    require_studio()
    safe_url(url)
    request = urllib.request.Request(url)  # No Authorization header is sent to media hosts.
    with urllib.request.build_opener(NoRedirects()).open(request, timeout=120) as response:
        if response.headers.get_content_type() not in ("video/mp4", "application/octet-stream"):
            raise ValueError("Fal media response is not an MP4")
        length = 0
        with output.open("xb") as stream:
            while block := response.read(1024 * 1024):
                length += len(block)
                if length > 200_000_000:
                    raise ValueError("Fal MP4 exceeded the 200 MB download bound")
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
    if length < 1024:
        raise ValueError("Fal MP4 is empty or truncated")


def load_budget_helper():
    if not LEDGER.is_file():
        raise ValueError(f"Authoritative budget ledger is missing: {LEDGER}; never create an independent budget")
    budget = json.loads(LEDGER.read_text())["budget"]
    if budget["currency"] != "USD" or not 0 < budget["cap_cents"] <= 10000:
        raise ValueError("The authoritative combined budget must remain at or below US$100")
    spec = importlib.util.spec_from_file_location("avatar_review_ledger", APP / "scripts/review-ledger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def activate(identity, output, expected_sha):
    if checksum(output) != expected_sha:
        raise ValueError("Avatar changed before activation")
    manifest = APP / "data/avatars.json"
    with lease(manifest.with_suffix(".json.lock")):
        value = json.loads(manifest.read_text())
        value[identity] = True
        atomic_json(manifest, value)


def run_job(identity, args, budget):
    portrait = APP / "public/img/portrait.jpg"
    provenance = APP / "data/presenter-assets.json"
    source = json.loads(provenance.read_text())["portrait"]
    if source["path"] != "public/img/portrait.jpg" or checksum(portrait) != source["sha256"]:
        raise ValueError("Portrait does not match the new presenter-assets provenance")
    audio = APP / f"public/audio/{identity}.mp3"
    metadata = probe(audio)
    streams = [stream for stream in metadata["streams"] if stream["codec_type"] == "audio"]
    if len(streams) != 1:
        raise ValueError("CTA source must have one audio stream")
    seconds = duration(metadata, streams[0])
    maximum = reserve_cents(seconds)
    inputs = {"id": identity, "portraitSha256": checksum(portrait), "audioSha256": checksum(audio),
              "presenterManifestSha256": checksum(provenance), "endpoint": ENDPOINT,
              "resolution": "720p", "audioSeconds": seconds,
              "inputTransportVersion": INPUT_TRANSPORT_VERSION}
    identity_hash = digest(inputs)
    job_directory = STATES / identity
    job_directory.mkdir(parents=True, exist_ok=True)
    state_path = job_directory / f"{identity_hash}.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["inputs"] != inputs:
            raise ValueError("Saved avatar inputs do not match their identity")
    else:
        if args.settle_usd is not None:
            raise ValueError("Settlement cannot create or submit a new avatar job")
        replacements = []
        for previous in job_directory.glob("*.json"):
            old = json.loads(previous.read_text())
            if old.get("phase") not in ("validated-needs-review", "billing-settled"):
                terminal_validation_proven(old)
                replacements.append({"path": str(previous), "sha256": checksum(previous),
                                     "requestId": old["request"]["request_id"],
                                     "reservationId": old["reservationId"]})
        state = {"schemaVersion": 1, "phase": "prepared", "createdAt": now(), "inputs": inputs,
                 "inputHash": identity_hash, "maximumCents": maximum,
                 "reservationId": f"lip-sync:{identity}:{identity_hash}", "ledger": str(LEDGER)}
        if replacements:
            state["replacesTerminalValidationFailures"] = replacements
        atomic_json(state_path, state)
    if state.get("phase") == "provider-validation-failed":
        raise ValueError("This request is terminal; retain its reservation and receipt for billing reconciliation")
    if args.settle_usd is not None and not state.get("output"):
        raise ValueError("Settle an existing validated clip, or use review-ledger.py for failed-request billing")
    reservation = json.loads(LEDGER.read_text())["budget"]["reservations"].get(state["reservationId"])
    if reservation is None:
        if state["phase"] != "prepared":
            raise ValueError("Saved request has no budget reservation; reconcile before continuing")
        budget.reserve(LEDGER, state["reservationId"], maximum, "lip_sync",
                       f"Fal Fabric 1.0 720p {identity}; at most audio+4s and 20s, $0.15/output-second")
    elif reservation["maximum_cents"] != maximum:
        raise ValueError("Existing avatar reservation does not match its bounded maximum")
    elif state["phase"] == "prepared" and reservation["state"] != "reserved":
        raise ValueError("A new POST requires an outstanding maximum-cost reservation")
    output = APP / f"public/avatar/{identity}.mp4"
    if state.get("output") and output.is_file() and checksum(output) == state["output"]["sha256"]:
        activate(identity, output, state["output"]["sha256"])
        print(f"SKIP verified {identity}; visual lip-sync review remains required", flush=True)
    else:
        body = prepare_uploads(state_path, state, portrait, audio)
        def submit():
            if (checksum(portrait) != inputs["portraitSha256"] or checksum(audio) != inputs["audioSha256"]
                    or checksum(provenance) != inputs["presenterManifestSha256"]):
                raise ValueError("Inputs changed before the paid POST; submission refused")
            validate_replacements(state)
            return saved_api_json(state_path, state, SUBMIT_URL, body)
        state = submit_once(state_path, state, submit)
        request = state["request"]
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", request_id):
            raise ValueError("Saved Fal request ID is invalid; reconcile the submission")
        status_url = safe_url(request["status_url"], "queue", request_id)
        response_url = safe_url(request["response_url"], "queue", request_id)
        deadline = time.monotonic() + args.poll_seconds
        while True:
            status = saved_api_json(state_path, state, status_url)
            state.update({"lastStatus": status.get("status"), "lastPolledAt": now()})
            atomic_json(state_path, state)
            print(f"{identity}: {status.get('status')}; request {request_id}", flush=True)
            if status.get("status") == "COMPLETED":
                if status.get("error"):
                    state["phase"] = "provider-failed"
                    atomic_json(state_path, state)
                    raise ValueError("Fal completed with an error; reservation retained for billing reconciliation")
                break
            if status.get("status") not in ("IN_QUEUE", "IN_PROGRESS"):
                raise ValueError("Unexpected Fal status; saved request and budget retained")
            if time.monotonic() >= deadline:
                raise ValueError("Polling deadline reached; resume this request with the same command")
            time.sleep(10)
        response = saved_api_json(state_path, state, response_url)
        url = safe_url(response.get("video", {}).get("url"))
        state.update({"phase": "result-received", "videoUrl": url, "resultReceivedAt": now()})
        atomic_json(state_path, state)
        temporary = job_directory / f".{uuid.uuid4().hex}.partial.mp4"
        try:
            download(url, temporary)
            validated = validate_output(probe(temporary), seconds)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(temporary),
                            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], check=True)
            if (checksum(portrait) != inputs["portraitSha256"] or checksum(audio) != inputs["audioSha256"]
                    or checksum(provenance) != inputs["presenterManifestSha256"]):
                raise ValueError("Presenter or audio changed while Fal was running; paid request retained, activation refused")
            output.parent.mkdir(parents=True, exist_ok=True)
            state.update({"phase": "validated-needs-review", "validatedAt": now(),
                          "qualityApproval": "pending-full-visual-and-lip-sync-review",
                          "output": {"path": str(output), "sha256": checksum(temporary),
                                     "bytes": temporary.stat().st_size, **validated},
                          "costEstimateCents": int((Decimal(str(validated["videoSeconds"])) * RATE_CENTS)
                                                   .to_integral_value(rounding=ROUND_CEILING)),
                          "billing": "reserved-until-confirmed-provider-cost"})
            # Both locations are on Studio's home volume; atomic replacement preserves prior files on failure.
            os.replace(temporary, output)
            atomic_json(state_path, state)
            activate(identity, output, state["output"]["sha256"])
            print(f"READY needs review {identity}; maximum ${maximum/100:.2f} remains reserved", flush=True)
        finally:
            temporary.unlink(missing_ok=True)
    if args.settle_usd is not None:
        actual = int(Decimal(args.settle_usd) * 100)
        if actual < 0 or not args.billing_receipt:
            raise ValueError("Settlement requires confirmed nonnegative cost and a provider billing reference")
        budget.settle(LEDGER, state["reservationId"], actual, args.billing_receipt)
        state.update({"phase": "billing-settled", "billing": {"actualCents": actual,
                      "receipt": args.billing_receipt, "settledAt": now()}})
        atomic_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--parallel", type=int, choices=(1, 2), default=1)
    parser.add_argument("--poll-seconds", type=int, default=1800)
    parser.add_argument("--settle-usd", help="Confirmed provider cost; one existing CTA only")
    parser.add_argument("--billing-receipt", help="Non-secret provider billing reference")
    args = parser.parse_args()
    ids = selected_ids(json.loads((APP / "data/ctas.json").read_text()), args.ids)
    if args.poll_seconds < 1 or args.poll_seconds > 10800:
        parser.error("--poll-seconds must be between 1 and 10800")
    if (args.settle_usd is not None or args.billing_receipt) and (len(ids) != 1 or not args.billing_receipt):
        parser.error("Settlement requires exactly one CTA and --billing-receipt")
    if args.billing_receipt and args.settle_usd is None:
        parser.error("--billing-receipt requires --settle-usd")
    if args.settle_usd is not None:
        try:
            amount = Decimal(args.settle_usd)
            if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
                raise ValueError()
        except (ValueError, ArithmeticError):
            parser.error("--settle-usd must be a nonnegative USD amount with at most two decimal places")
    if not args.execute:
        print(json.dumps({"status": "planned-no-media-or-api-work", "ids": ids, "parallel": args.parallel,
                          "endpoint": ENDPOINT, "resolution": "720p", "rateUsdPerOutputSecond": 0.15,
                          "maximumOutputSeconds": MAX_OUTPUT_SECONDS, "ledger": str(LEDGER),
                          "inputTransportVersion": INPUT_TRANSPORT_VERSION,
                          "storageSdk": f"fal-client=={FAL_CLIENT_VERSION}",
                          "presenterManifest": "data/presenter-assets.json"}, indent=2))
        return
    require_studio()
    os.nice(10)
    budget = load_budget_helper()
    with lease(STATES / ".runner.lock"):
        failures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {executor.submit(run_job, identity, args, budget): identity for identity in ids}
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    failures.append(f"{futures[future]}: {error}")
        if failures:
            raise ValueError("\n".join(failures))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
