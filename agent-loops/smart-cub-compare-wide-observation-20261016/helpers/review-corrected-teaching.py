#!/usr/bin/env python3
"""Review registered corrections through unchanged review-media.py. Plan is free."""
import argparse
import importlib.util
import json
from pathlib import Path
import re


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ledger = module("corrected_review_ledger", "review-ledger.py")
media = module("corrected_review_media", "review-media.py")
overrides = ledger.overrides


def inventory(root, authority, hash_media=False):
    registrations = ledger.read(authority).get("teaching_source_overrides", {})
    resolved = overrides.load(root, registrations, hash_media)
    items = media.inventory(root, root / overrides.PUBLISHED_MANIFEST)
    return overrides.review_items(items, resolved, overrides.implementation(root) if resolved else {})


def refuse_uncertain_prior(root, identity, aspect):
    directory = root / "out/review-complete" / re.sub(r"[^A-Za-z0-9_.-]", "_", identity) / aspect
    for path in directory.glob("*/request-*.json"):
        job = ledger.read(path)
        if job.get("state") != "completed_advisory" and (job.get("reservation_id") or job.get("state") == "submitted_unknown"):
            raise ValueError("Prior paid request for this lesson/aspect is unresolved; reconcile it before corrected review")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("plan", "technical", "model"), default="plan")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--id", choices=sorted(overrides.ALLOWED), required=True)
    parser.add_argument("--aspect", choices=("wide", "tall", "both"), default="both")
    parser.add_argument("--execute", action="store_true", help="Explicitly allow Studio media/API work")
    parser.add_argument("--max-requests", type=int, default=1)
    args = parser.parse_args()
    root, authority = args.root.resolve(), args.ledger.resolve()
    identity = "teaching:" + args.id
    items = inventory(root, authority)
    item = items[identity]
    if "source_correction" not in item["context"]:
        parser.error("Target has no explicitly registered correction")
    aspects = ledger.ASPECTS if args.aspect == "both" else (args.aspect,)
    if args.mode == "plan":
        print(json.dumps({"state": "plan-only", "id": identity, "outputs": item["outputs"],
                          "source_override_sha256": item["context"]["source_correction"]["sha256"]}, indent=2))
        return
    if not args.execute:
        parser.error("Media/API work needs --execute on Studio")
    ledger.require_studio()
    item = inventory(root, authority, True)[identity]
    client, model_info = None, None
    if args.mode == "model":
        # The unchanged module owns durable intent, exact cache reuse, reservation,
        # settlement, uncertain-POST protection and provider-file cleanup.
        media.tariff_current()
        if args.max_requests <= 0:
            parser.error("--max-requests must be positive")
        for aspect in aspects:
            refuse_uncertain_prior(root, identity, aspect)
        client = media.Gemini()
        model_info = client.model()
    requests = 0
    for aspect in aspects:
        if args.mode == "model" and requests >= args.max_requests:
            print(json.dumps({"state": "batch_request_limit_reached", "paid_requests": requests}))
            break
        # Require current exact source/CTA/derivative receipt and mechanical proof
        # before either technical review or a paid call.
        correction = module("correction_receipt", "blend-corrected-teaching.py")
        correction.preflight_output(root, authority, args.id, aspect)
        result = media.review_one(root, authority, item, aspect, root / "out/review-complete",
                                  client, model_info, technical_only=args.mode == "technical")
        requests += result["paid_requests"]
        print(json.dumps(result), flush=True)
        if result["state"] not in {"completed_advisory", "skipped_exact_version_advisory", "technical_clear"}:
            raise ValueError("Correction review stopped without a complete advisory: " + str(result["state"]))


if __name__ == "__main__":
    main()
