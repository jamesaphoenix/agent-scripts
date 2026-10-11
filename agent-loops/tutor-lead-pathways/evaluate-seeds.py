#!/usr/bin/env python3
"""Offline, hash-bound seed admission replay. Never fetches or calls a provider."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time


def evaluate_decisions(cases, decisions):
    urls = [case["url"] for case in cases]
    if not urls or len(urls) > 500 or len(set(urls)) != len(urls):
        raise ValueError("Fixture must contain one to five hundred distinct URLs")
    if any(type(case.get("permit")) is not bool for case in cases):
        raise ValueError("Every frozen case needs a boolean judgement")
    permitted = decisions["permitted"]
    rejected = [row["url"] for row in decisions["rejected"]]
    if collections.Counter(permitted + rejected) != collections.Counter(urls):
        raise ValueError("Decisions must cover every input exactly once")
    admitted = set(permitted)
    errors = [case["url"] for case in cases if (case["url"] in admitted) != case["permit"]]
    return {"cases": len(cases), "admissionErrors": len(errors), "errorUrls": errors,
            "htmlRejected": sum(case["permit"] and case["url"] not in admitted for case in cases),
            "unsupportedAdmitted": sum(not case["permit"] and case["url"] in admitted for case in cases)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    raw = args.fixture.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != args.sha256:
        raise ValueError("Frozen fixture SHA256 changed")
    cases = json.loads(raw)["cases"]
    # Validate the fixture before invoking even the offline CLI.
    evaluate_decisions(cases, {"permitted": [case["url"] for case in cases], "rejected": []})
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as directory:
        seeds = Path(directory) / "seeds.txt"
        seeds.write_text("\n".join(case["url"] for case in cases) + "\n")
        result = subprocess.run([str(args.binary.resolve()), "filter-seeds", "--seeds", str(seeds)],
                                capture_output=True, text=True, check=True, timeout=60)
    report = evaluate_decisions(cases, json.loads(result.stdout))
    report.update(fixtureSha256=digest,
                  evaluatorSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  binarySha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),
                  elapsedSeconds=round(time.monotonic() - started, 6), paidUsd=0,
                  scope="Offline seed admission replay; no live lead-yield claim")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
