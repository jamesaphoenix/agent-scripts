#!/usr/bin/env python3
"""Create an immutable four-API-path plan and import historic paid receipts."""
import argparse
import decimal
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
import tomllib

from discovery import PaidCache, atomic_json, file_hash, request_key


def build_plan(towns):
    plan = []
    def add(lane, town, payload, round_number):
        plan.append({"lane": lane, "town": town["name"], "round": round_number,
                     "key": request_key(lane, payload), "payload": payload})
    # DataForSEO decodes raw '+' as a space. Encode the literal 11+ query.
    for round_number, keyword in enumerate(["11%2B tutor", "tutor", "11 plus tutor", "eleven plus tuition", "primary tutor", "private tutor"]):
        for town in towns:
            add("maps", town, {"keyword": f"{keyword} {town['name']} Buckinghamshire", "location_coordinate": town["coordinate"] + ",13z", "language_code": "en", "se_domain": "google.co.uk", "depth": 100, "search_places": False, "search_this_area": True}, round_number)
    for offset in [0, 100]:
        for town in towns:
            add("business", town, {"categories": ["tutoring_service", "private_tutor"], "location_coordinate": town["coordinate"] + ",12", "limit": 100, "offset": offset}, offset // 100)
    organic = [
        '11%2B tutor {town} Buckinghamshire',
        'tutor {town} Buckinghamshire',
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
    return plan


def completed_paid_history(history_root, legacy_exposure, exclude=None):
    receipts, charges, inherited_floors = [], {}, []
    largest_exposure = legacy_exposure
    folders = set(history_root.glob("tutor-lead-pathways-*")) | set(history_root.glob("tutor-search-entrypoints-*"))
    for folder in sorted(folders):
        if exclude is not None and folder.resolve() == exclude.resolve():
            continue
        database = folder / "paid-snapshot-final.sqlite"
        if not database.exists():
            database = folder / "paid.sqlite"
        if not database.exists():
            continue
        runtime = folder / "runtime.json"
        if not runtime.exists() or json.loads(runtime.read_text()).get("phase") != "complete":
            raise ValueError("Historical paid run is not complete: " + folder.name)
        lock_path = folder / "run.lock"
        if lock_path.exists():
            with lock_path.open() as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("Historical paid run still has an owner: " + folder.name) from None
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT * FROM requests").fetchall()
            prior = connection.execute("SELECT prior FROM settings WHERE id=1").fetchone()["prior"]
        finally:
            connection.close()
        inherited_floors.append(prior)
        local_spend = 0
        for row in rows:
            if row["status"] not in ("succeeded", "failed") or not row["receipt"]:
                raise ValueError("Historical paid request is unsettled: " + folder.name)
            path = folder / "receipts" / (row["key"] + ".json")
            if not path.exists() or file_hash(path) != row["receipt_hash"]:
                raise ValueError("Historical receipt changed: " + folder.name)
            saved = json.loads(path.read_text())
            if (saved["key"] != row["key"] or saved["lane"] != row["lane"] or
                    saved["payload"] != json.loads(row["payload"]) or request_key(saved["lane"], saved["payload"]) != row["key"]):
                raise ValueError("Historical request identity changed: " + folder.name)
            response = saved["response"]
            if len(response.get("tasks", [])) != 1 or response["tasks"][0].get("id") != row["task_id"]:
                raise ValueError("Historical task identity changed: " + folder.name)
            receipts.append((row["lane"], saved["payload"], response))
            if not row["imported"]:
                cost = decimal.Decimal(str(response.get("cost")))
                if (not cost.is_finite() or cost < 0 or decimal.Decimal(str(response["tasks"][0].get("cost"))) != cost or
                        decimal.Decimal(row["cost_usd"]) != cost or
                        row["cost_cents"] != int((cost * 100).to_integral_value(rounding=decimal.ROUND_CEILING))):
                    raise ValueError("Historical ledger charge changed: " + folder.name)
                local_spend += row["cost_cents"]
                if row["task_id"] in charges and charges[row["task_id"]] != row["cost_cents"]:
                    raise ValueError("Historical task charge changed")
                charges[row["task_id"]] = row["cost_cents"]
        largest_exposure = max(largest_exposure, prior + local_spend)
    # Inherited allowances account for missing older archives. Unique new task
    # charges also account for parallel runs sharing the same inherited floor.
    floor = max(legacy_exposure, min(inherited_floors, default=legacy_exposure))
    return max(largest_exposure, floor + sum(charges.values())), receipts


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
    prior, completed_receipts = completed_paid_history(history_root, prior, exclude=root)
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
    for lane, payload, envelope in completed_receipts:
        cache.import_receipt(lane, payload, envelope)
    imported = cache.usage()["importedReceipts"]
    cache.db.close()
    towns = json.loads(towns_file.read_text())
    plan = build_plan(towns)
    atomic_json(root / "plan.json", plan)
    config_path = Path.home() / ".codex/config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    manifest = {"runId": "bucks-five-pathways-20261011", "hours": 6, "budgetCents": 2000, "priorExposureCents": prior,
                "sharedCapCents": 10000, "planSha256": file_hash(root / "plan.json"), "towns": towns,
                "codexBinary": str(codex_binary), "codexModel": model or config.get("model"),
                "nodeBinary": shutil.which("node"),
                "disabledMcpServers": list(config.get("mcp_servers", {})), "crawlerBinary": str(crawler_binary),
                "historyRoot": str(history_root), "importedHistoricalReceipts": imported,
                "maximumSdkTurns": 12, "sdkTokenCeiling": 2000000, "freePageBudget": 10000,
                "crawlLimits": {"depth": 2, "branchSites": 15, "sites": 50, "batchPages": 400,
                                "pagesPerSite": 8, "workers": 4, "rootsPerBatch": 10},
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
