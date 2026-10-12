#!/usr/bin/env python3
"""Prepare a separate finite stage from sealed, hash-verified public evidence."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from directory_profiles import admitted_url, decode_page, export_csv, import_seed_batches, merge_profiles, now, profile_locality_status, exclusion_reason, profile_record, save


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(parent, output, hours, new_pages, target, seed_batch=None, source_run_dir=None):
    if not 0 < hours <= 6 or not 1 <= new_pages <= 5000 or not 1 <= target <= 5000:
        raise ValueError("Choose bounded duration, additional attempts and target")
    if output.exists():
        raise ValueError("Follow-on stage already exists; resume it without re-preparing")
    with (parent / "owner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = json.loads((parent / "status.json").read_text())
        state = json.loads((parent / "state.json").read_text())
        if status.get("phase") != "stage_complete" or state.get("phase") != "stage_complete":
            raise ValueError("Parent stage must be terminal")
        if any(admitted_url(u) != u for u in state["queue"]):
            raise ValueError("Parent frontier left admitted public paths")
        if seed_batch and (seed_batch.stat().st_size > 2 * 1024 * 1024 or digest(seed_batch) != seed_batch.stem):
            raise ValueError("New source seed batch hash changed or exceeded its bound")
        page_limit = state["attempts"] + new_pages
        if page_limit > 10000:
            raise ValueError("Inherited and additional attempts exceed the stage ceiling")
        receipts = []
        for path in sorted((parent / "receipts").glob("*.json")):
            receipt = json.loads(path.read_text())
            if admitted_url(receipt["url"]) != receipt["url"] or path.stem != hashlib.sha256(receipt["url"].encode()).hexdigest():
                raise ValueError("Cached receipt URL hash does not match")
            body = parent / "pages" / (path.stem + ".html")
            if not receipt.get("error") and digest(body) != receipt.get("sha256"):
                raise ValueError("Cached page hash does not match")
            receipts.append((path, body if not receipt.get("error") else None))
        provenance = {"runDir": str(parent), "jobSha256": digest(parent / "job.json"),
                      "stateSha256": digest(parent / "state.json"), "statusSha256": digest(parent / "status.json")}
        replayed = []
        for row in state["records"]:
            url = row["sourceUrls"][0]
            key = hashlib.sha256(url.encode()).hexdigest()
            receipt = json.loads((parent / "receipts" / (key + ".json")).read_text())
            parsed = profile_record(decode_page((parent / "pages" / (key + ".html")).read_bytes(), receipt.get("contentType", "")), url)
            if not parsed or parsed["personId"] != row["personId"] or parsed["platform"] != row["platform"]:
                raise ValueError("Parent profile does not match its cached person evidence")
            parsed["pageEvidence"] = row["pageEvidence"]
            replayed.append(parsed)
        state["records"] = replayed
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = Path(tempfile.mkdtemp(prefix="." + output.name + "-prepare-", dir=output.parent))
        try:
            for folder in ("pages", "receipts", "seed-batches"):
                (temporary / folder).mkdir(mode=0o700)
            for receipt, body in receipts:
                # Cache bodies are immutable. New URLs get separate files in this stage.
                # Receipts are copied so incidental metadata writes cannot affect history.
                shutil.copy2(receipt, temporary / "receipts" / receipt.name)
                if body:
                    os.link(body, temporary / "pages" / body.name)
            for batch in (parent / "seed-batches").glob("*.json"):
                if digest(batch) != batch.stem:
                    raise ValueError("Source seed batch hash changed")
                shutil.copy2(batch, temporary / "seed-batches" / batch.name)
            if seed_batch:
                shutil.copy2(seed_batch, temporary / "seed-batches" / seed_batch.name)
                import_seed_batches(temporary, state)
            if not state["queue"]:
                raise ValueError("Follow-on stage has no new public frontier")
            started = datetime.datetime.now(datetime.timezone.utc)
            job = {"startedAt": started.isoformat(), "deadlineUnix": started.timestamp() + hours * 3600,
                   "pageLimit": page_limit, "initialAttemptCount": state["attempts"], "newAttemptLimit": new_pages,
                   "target": target, "seeds": list(state["queue"]), "paidBudget": 0, "parentStage": provenance,
                   "scope": "Public individual tutor profiles across subjects in Bucks and Milton Keynes. Independent locality and consent verification remain required."}
            if source_run_dir:
                job["seedSourceRun"] = str(source_run_dir)
            state["phase"] = "running"
            state["updatedAt"] = now()
            profiles = merge_profiles(state["records"])
            qualified = [r for r in profiles if profile_locality_status(r) == "advertised_bucks_locality"
                         and not r.get("locationConflict") and not exclusion_reason(r)]
            save(temporary / "job.json", job)
            save(temporary / "state.json", state)
            save(temporary / "profiles.json", profiles)
            save(temporary / "bucks-private-tutors.json", qualified)
            export_csv(temporary, qualified)
            save(temporary / "status.json", {"phase": "prepared", "observedAt": now(), "pid": 0,
                "attempts": state["attempts"], "queue": len(state["queue"]), "bucksPrivateProfiles": len(qualified), "paidCostUsd": 0})
            result = {"phase": "prepared", "at": now(), "parentStage": provenance,
                      "newStageJobSha256": digest(temporary / "job.json"), "cachedReceiptsReused": len(receipts),
                      "parentRowsReplayed": len(replayed),
                      "paidCalls": 0, "originalParentDeadlineRetained": True}
            save(temporary / "preparation.json", result)
            temporary.rename(output)
        except BaseException:
            shutil.rmtree(temporary)
            raise
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=2)
    parser.add_argument("--additional-pages", type=int, default=2000)
    parser.add_argument("--target", type=int, default=1500)
    parser.add_argument("--seed-batch", type=Path)
    parser.add_argument("--source-run-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.parent_dir, args.run_dir, args.hours, args.additional_pages, args.target, args.seed_batch, args.source_run_dir)))
