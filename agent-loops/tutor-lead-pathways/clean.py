#!/usr/bin/env python3
"""Separate listed county addresses from neighbouring and unresolved research."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request

from discovery import NoRedirect, atomic_json, hold_reason, now_iso, parse_time, subject_hint

POSTCODES_URL = "https://api.postcodes.io/postcodes"
DISTRICTS = {"E06000060", "E06000042"}  # Buckinghamshire and Milton Keynes.
POSTCODE = re.compile(r"\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b", re.I)


def normalise_postcode(value):
    return re.sub(r"\s+", "", value.upper())


def listed_addresses(record):
    values = [record.get("advertisedAddress")]
    values.extend(branch.get("advertisedAddress") for branch in record.get("establishments", []))
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value.strip()))


def postcode_cache_path(root, postcode):
    digest = hashlib.sha256(normalise_postcode(postcode).encode()).hexdigest()
    return root / "geography/postcodes" / (digest + ".json")


def lookup_postcodes(root, postcodes, deadline=None):
    results, missing = {}, []
    for postcode in sorted(set(map(normalise_postcode, postcodes))):
        path = postcode_cache_path(root, postcode)
        if path.exists():
            saved = json.loads(path.read_text())
            if saved.get("postcode") != postcode:
                raise ValueError("Cached postcode identity changed")
            results[postcode] = saved
        else:
            missing.append(postcode)
    for offset in range(0, len(missing), 100):
        batch = missing[offset:offset + 100]
        remaining = deadline - time.time() if deadline else 20
        if remaining <= 0:
            break
        try:
            request = urllib.request.Request(POSTCODES_URL, json.dumps({"postcodes": batch}).encode(),
                                             {"Content-Type": "application/json"})
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=min(20, remaining)) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Geography response exceeded two MB")
            envelope = json.loads(raw)
            items = envelope.get("result")
            if envelope.get("status") != 200 or not isinstance(items, list):
                raise ValueError("Unsuccessful postcode lookup")
            mapped = {normalise_postcode(item["query"]): item.get("result") for item in items}
            if set(mapped) != set(batch):
                raise ValueError("Postcode response did not match its queries")
            for postcode in batch:
                result = mapped[postcode]
                if result is not None and normalise_postcode(result["postcode"]) != postcode:
                    raise ValueError("Returned postcode identity mismatch")
                saved = {"postcode": postcode, "source": POSTCODES_URL, "observedAt": now_iso(), "result": result}
                atomic_json(postcode_cache_path(root, postcode), saved)
                results[postcode] = saved
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Preserve failed observations, continue other batches, and never silently
            # turn an unknown postcode into an in-county business.
            for postcode in batch:
                saved = {"postcode": postcode, "source": POSTCODES_URL, "observedAt": now_iso(), "result": None,
                         "error": type(error).__name__ + ": postcode lookup unavailable"}
                atomic_json(postcode_cache_path(root, postcode), saved)
                results[postcode] = saved
    return results


def county_evidence(record, geography):
    observations, local_addresses = [], []
    for address in listed_addresses(record):
        for match in POSTCODE.finditer(address):
            postcode = normalise_postcode(match[0])
            saved = geography.get(postcode, {})
            result = saved.get("result") or {}
            district = (result.get("codes") or {}).get("admin_district")
            local = district in DISTRICTS
            observation = {"postcode": postcode, "address": address, "districtCode": district,
                           "district": result.get("admin_district"), "inTargetCounty": local if district else None,
                           "source": POSTCODES_URL, "observedAt": saved.get("observedAt")}
            observations.append(observation)
            if local and address not in local_addresses:
                local_addresses.append(address)
    status = "has_bucks_listing" if local_addresses else "outside_recorded_listings" if observations and all(
        item["inTargetCounty"] is False for item in observations) else "needs_location_review"
    return {"status": status, "localAddresses": local_addresses, "observations": observations,
            "qualification": "This locates advertised establishment postcodes. It does not verify current operation, service area, subjects or ownership."}


def write_csv(path, rows):
    fields = ["name", "website", "phone", "email", "emailClaims", "localAddress", "advertisedAddress", "category", "status",
              "subjectEvidence", "servicesClaim", "localityClaim", "verificationNotes", "currentActivityVerified",
              "countyStatus", "newInThisRun", "sourceUrl", "holdReason", "reviewed", "independentVerified", "outreachEligible"]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def clean_exports(root, deadline=None):
    records = json.loads((root / "exports/businesses.json").read_text())
    postcodes = [match[0] for row in records for address in listed_addresses(row) for match in POSTCODE.finditer(address)]
    geography = lookup_postcodes(root, postcodes, deadline)
    local, subject, unresolved, outside, held, agent = [], [], [], [], [], []
    for record in records:
        reason = hold_reason(record)
        if reason:
            record["status"], record["holdReason"] = "held", reason
        sources = record.get("provenance", [])
        record["verificationNotes"] = "; ".join(dict.fromkeys(s["notes"] for s in sources if isinstance(s.get("notes"), str)))
        record["localityClaim"] = "; ".join(dict.fromkeys(s["localityClaim"] for s in sources if isinstance(s.get("localityClaim"), str)))
        record["servicesClaim"] = "; ".join(sorted({v for s in sources for v in (s.get("servicesClaim") or []) if isinstance(v, str)}))
        # Fetch time, a Maps listing and an agent's claim do not prove current activity.
        record["currentActivityVerified"] = False
        record["subjectEvidence"] = subject_hint(record)
        record["countyEvidence"] = county_evidence(record, geography)
        record["countyStatus"] = record["countyEvidence"]["status"]
        record["localAddress"] = "; ".join(record["countyEvidence"]["localAddresses"])
        record["newInThisRun"] = not any(source.get("historical") for source in record.get("provenance", []))
        record["emailClaims"] = "; ".join(sorted({c["value"] for c in record.get("contactClaims", []) if c.get("type") == "mail"}))
        if any(source.get("lane") == "codex_oauth" for source in sources):
            agent.append(record)
        if record.get("status") == "held" or record.get("sourceOnly"):
            held.append(record)
        elif record["countyStatus"] == "has_bucks_listing":
            local.append(record)
            if record["subjectEvidence"] in ("11_plus_hint", "primary_hint"):
                subject.append(record)
        elif record["countyStatus"] == "outside_recorded_listings":
            outside.append(record)
        else:
            unresolved.append(record)
    output = root / "exports"
    atomic_json(output / "cleaned-businesses.json", records)
    for name, rows in [("bucks-listings-research", local), ("local-11-plus-primary-research", subject),
                       ("needs-location-review", unresolved), ("outside-recorded-area", outside), ("held", held),
                       ("agent-researched-candidates", agent)]:
        write_csv(output / (name + ".csv"), rows)
    summary = {"observedAt": now_iso(), "businessIdentities": len(records), "countyListingsResearch": len(local),
               "localSubjectHints": len(subject), "needsLocationReview": len(unresolved), "outsideRecordedArea": len(outside),
               "heldOrSourceOnly": len(held), "newCountyListingsResearch": sum(r["newInThisRun"] for r in local),
               "postcodeQueries": len(set(map(normalise_postcode, postcodes))),
               "postcodeLookupFailures": sum(bool(r.get("error")) for r in geography.values()),
               "countyStatusCounts": dict(collections.Counter(r["countyStatus"] for r in records)),
               "heldReasonCounts": dict(collections.Counter(r.get("holdReason") or "source_only" for r in held)),
               "agentCandidateIdentities": len(agent), "agentCandidatesHeld": sum(r.get("status") == "held" for r in agent),
               "qualification": "Research candidates only. County listing and subject hints do not verify independence, current services, contacts or consent."}
    atomic_json(output / "cleaning-summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    job = json.loads((args.run_dir / "job.json").read_text())
    print(json.dumps(clean_exports(args.run_dir, parse_time(job["deadline"]).timestamp())))
