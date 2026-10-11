#!/usr/bin/env python3
"""Create an immutable four-API-path plan and import historic paid receipts."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tomllib

from discovery import PaidCache, atomic_json, file_hash, request_key


def prepare(root, history_root, towns_file, codex_binary, crawler_binary, model):
    if (root / "manifest.json").exists():
        raise ValueError("Prepared job already exists; preserve its original plan")
    checkpoints = list(history_root.glob("tracelearn-tutors-*/discovery.json"))
    prior, receipts = 0, []
    for path in checkpoints:
        state = json.loads(path.read_text())
        for intent in state["intents"]:
            if intent["status"] in ("submitting", "reserved", "uncertain"):
                raise ValueError("Prior run contains an unsettled paid request")
            prior += intent.get("costCents", 0)
        for folder in path.parent.glob("*-receipts"):
            receipts.extend(folder.glob("*.json"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = PaidCache(root, 2000, prior)
    imported = 0
    for path in receipts:
        saved = json.loads(path.read_text())
        if "response" not in saved:
            continue
        envelope = saved["response"].get("raw")
        if isinstance(envelope, str):
            envelope = json.loads(envelope)
        if not envelope:
            continue
        data = envelope.get("tasks", [{}])[0].get("data", {})
        lane = data.get("se_type")
        if lane not in ("organic", "maps"):
            continue
        payload = {k: data[k] for k in ["keyword", "language_code", "location_coordinate", "se_domain", "depth", "search_places", "search_this_area"] if k in data}
        cache.import_receipt(lane, payload, envelope)
        imported += 1
    towns = json.loads(towns_file.read_text())
    plan = []
    def add(lane, town, payload, round_number):
        plan.append({"lane": lane, "town": town["name"], "round": round_number, "key": request_key(lane, payload), "payload": payload})
    for round_number, keyword in enumerate(["11 plus tutor", "eleven plus tuition", "primary tutor", "private tutor"]):
        for town in towns:
            add("maps", town, {"keyword": f"{keyword} {town['name']} Buckinghamshire", "location_coordinate": town["coordinate"] + ",13z", "language_code": "en", "se_domain": "google.co.uk", "depth": 100, "search_places": False, "search_this_area": True}, round_number)
    for offset in [0, 100]:
        for town in towns:
            add("business", town, {"categories": ["tutoring_service", "private_tutor"], "location_coordinate": town["coordinate"] + ",12", "limit": 100, "offset": offset}, offset // 100)
    organic = [
        '"11 plus" "{town}" (tutor OR tuition) -kumon -site:superprof.co.uk -site:tutorful.co.uk -site:tutorhunt.com',
        '"primary" "tutor" "{town}" ("independent" OR "qualified teacher" OR "retired teacher") -kumon',
    ]
    directories = [
        '"{town}" (tutor OR tuition) (site:directory.familyinfo.buckinghamshire.gov.uk OR site:chilternchatter.com OR site:haddenham.net)',
        '"{town}" ("11 plus" OR "primary tutor") (filetype:pdf OR inurl:newsletter OR inurl:directory)',
    ]
    for lane, variants in [("organic", organic), ("directories", directories)]:
        for round_number, variant in enumerate(variants):
            for town in towns:
                add(lane, town, {"keyword": variant.format(town=town["name"]), "location_coordinate": town["coordinate"] + ",200", "language_code": "en", "se_domain": "google.co.uk", "depth": 20}, round_number)
    atomic_json(root / "plan.json", plan)
    config_path = Path.home() / ".codex/config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    manifest = {"runId": "bucks-five-pathways-20261011", "hours": 6, "budgetCents": 2000, "priorExposureCents": prior,
                "sharedCapCents": 10000, "planSha256": file_hash(root / "plan.json"), "towns": towns,
                "codexBinary": str(codex_binary), "codexModel": model or config.get("model"),
                "nodeBinary": shutil.which("node"),
                "disabledMcpServers": list(config.get("mcp_servers", {})), "crawlerBinary": str(crawler_binary),
                "historyRoot": str(history_root), "importedHistoricalReceipts": imported,
                "maximumSdkTurns": 12, "sdkTokenCeiling": 2000000, "freePageBudget": 4000,
                "pathways": ["maps", "business", "organic", "directories", "codex_oauth"],
                "monitorIntervalSeconds": 1800, "delivery": "private research artifacts, no external outreach"}
    atomic_json(root / "manifest.json", manifest)
    shutil.copy2(towns_file, root / "towns.json")
    print(json.dumps({"planQueries": len(plan), "locations": len(towns), "importedReceipts": imported, "priorExposureCents": prior, "budgetCents": 2000}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--towns", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--crawler", type=Path, required=True)
    parser.add_argument("--model")
    args = parser.parse_args()
    prepare(args.run_dir, args.history_root, args.towns, args.codex, args.crawler, args.model)
