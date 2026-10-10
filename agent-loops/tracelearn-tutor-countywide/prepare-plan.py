#!/usr/bin/env python3
"""Build a fresh plan without replaying any previously submitted search."""
import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path


def search_digest(query):
    # Source labels, town labels and requested depth do not make an old paid
    # search new. Skip an old search even if its outcome was failed or unknown.
    key = {"engine": query["engine"], "keyword": " ".join(query["keyword"].casefold().split()),
           "coordinate": query["coordinate"]}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def prepare(plan, histories, now):
    cache, exposure, costs, runs = set(), 0, 0.0, set()
    for state in histories:
        if state["runId"] in runs:
            continue
        runs.add(state["runId"])
        if datetime.datetime.fromisoformat(state["deadlineAt"].replace("Z", "+00:00")) > now:
            raise ValueError("Previous discovery window is still active")
        for intent in state["intents"]:
            status = intent["status"]
            if status == "planned":
                continue
            cache.add(search_digest(intent["query"]))
            if status in ("succeeded", "failed"):
                cents = intent["costCents"]
                if type(cents) is not int or cents < 0:
                    raise ValueError("Invalid settled historical charge")
                exposure += cents
                cost = intent.get("costUsd")
                if cost is not None:
                    if not math.isfinite(cost) or cost < 0 or math.ceil(cost * 100) != cents:
                        raise ValueError("Historical charge does not match receipt ledger")
                    costs += cost
            elif status in ("reserved", "submitting", "uncertain"):
                if intent["maximumCents"] != 100:
                    raise ValueError("Invalid historical reservation")
                exposure += 100
            else:
                raise ValueError("Unknown historical intent status")
    output, new = [], set()
    for query in plan:
        digest = search_digest(query)
        if digest not in cache and digest not in new:
            output.append(query)
            new.add(digest)
    if not output or len(output) > 400:
        raise ValueError("Fresh plan must contain 1..400 distinct searches")
    budget = min(2000, 10000 - exposure)
    if budget < 100:
        raise ValueError("Shared USD100 ceiling cannot admit another search")
    return output, {"priorExposureCents": exposure, "priorReportedCostUsd": costs,
                    "budgetCents": budget, "sharedCeilingCents": 10000,
                    "historicalRuns": sorted(runs), "searchCacheEntries": len(cache),
                    "excludedSearches": len(plan) - len(output),
                    "queries": len(output), "queryKinds": sorted({q["sourceKind"] for q in output})}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--history", required=True, action="append", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    now = datetime.datetime.now(datetime.timezone.utc)
    plan, manifest = prepare(json.loads(args.plan.read_text()),
                             [json.loads(p.read_text()) for p in args.history], now)
    args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(plan, indent=2) + "\n").encode()
    manifest["planSha256"] = hashlib.sha256(raw).hexdigest()
    manifest["preparedAt"] = now.isoformat()
    for name, data in [("queries.json", raw), ("plan-manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())]:
        target = args.out / name
        if target.exists():
            raise ValueError("Prepared inputs already exist; never reset a run")
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(0o600)
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
