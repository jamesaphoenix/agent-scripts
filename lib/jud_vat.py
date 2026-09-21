#!/usr/bin/env python3
"""Read-only JUD VAT preparation and portal credential discovery. No VAT writes."""
import argparse
from datetime import date, datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = Path(os.environ.get("JUD_INVOICE_COLLECTOR", str(ROOT.parent / "freeagent-invoice-collector")))
LOGIN_INDEX = "op://web-logins/sij4pezcijcyvqthv2epk3holu/notesPlain"
COMPANY_ID = "486529"

def validate_company(company):
    name = "".join(c for c in company.get("name", "").casefold() if c.isalnum())
    company_id = str(company.get("id", ""))
    if name != "justunderstandingdataltd" or company_id != COMPANY_ID or company.get("subdomain") != "jus001":
        raise ValueError("FreeAgent company is not verified JUD; stop before collecting or changing records")

def period(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("Period start must not follow period end")
    return first.isoformat(), last.isoformat()

def select_logins(index, hostname, username=None):
    if "/" in hostname or "@" in hostname or not hostname:
        raise ValueError("Supply an exact hostname, not a URL or email")
    matches = [entry for entry in index["entries"]
               if entry["website_host"].lower().removeprefix("www.") == hostname.lower().removeprefix("www.")
               and (username is None or entry["username"] == username)]
    unique = {(entry["vault_id"], entry["item_id"]): entry for entry in matches}
    return list(unique.values())

def load_fa():
    path = COLLECTOR / "scripts/fa.py"
    if not path.is_file():
        raise ValueError(f"Collector not found: {COLLECTOR}. Set JUD_INVOICE_COLLECTOR to its checkout.")
    spec = importlib.util.spec_from_file_location("jud_freeagent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")

def snapshot(fa, company, start, end, output):
    start, end = period(start, end)
    vat = fa.api("GET", "/vat_returns/" + end)["vat_return"]
    if vat.get("period_starts_on") != start or vat.get("period_ends_on") != end:
        raise ValueError("Requested dates do not match the actual FreeAgent VAT return")
    accounts = fa.api("GET", "/bank_accounts")["bank_accounts"]
    explanations, transactions = [], []
    for account in accounts:
        params = {"bank_account": account["url"], "from_date": start, "to_date": end}
        for path, key, target in [("/bank_transaction_explanations", "bank_transaction_explanations", explanations),
                                  ("/bank_transactions", "bank_transactions", transactions)]:
            for row in fa.api_get_all(path, params, key):
                row["_bank_account_name"] = account.get("name")
                row["_bank_account_currency"] = account.get("currency")
                target.append(row)
    payload = {"captured_at": datetime.now(timezone.utc).isoformat(), "company": company, "vat_return": vat,
               "bank_accounts": accounts, "bank_transaction_explanations": explanations, "bank_transactions": transactions}
    private_json(output, payload)
    print(json.dumps({"snapshot": str(output), "company": company["name"], "filing_status": vat.get("filing_status"),
                      "explanations": len(explanations), "bank_transactions": len(transactions)}))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Verify live company identity without changing FreeAgent")
    snap = commands.add_parser("snapshot", help="Save a private read-only snapshot for an explicit VAT period")
    snap.add_argument("start"); snap.add_argument("end"); snap.add_argument("--output", type=Path, required=True)
    login = commands.add_parser("login", help="Return exact-host login references, never resolved passwords")
    login.add_argument("hostname"); login.add_argument("--username")
    args = parser.parse_args()
    if args.command == "login":
        result = subprocess.run(["op", "read", LOGIN_INDEX], capture_output=True, text=True)
        if result.returncode:
            raise ValueError("Cannot read the web-login index with the existing service account")
        matches = select_logins(json.loads(result.stdout), args.hostname, args.username)
        if not matches:
            raise ValueError("No exact-host login found; check the hostname/account rather than trying unrelated credentials")
        print(json.dumps({"matches": matches, "selection_required": len(matches) != 1}, indent=2))
        return
    if args.command == "snapshot":
        period(args.start, args.end)
        if args.output.exists():
            raise ValueError("Snapshot exists; choose a new file to preserve the earlier baseline")
    fa = load_fa()
    company = fa.api("GET", "/company")["company"]
    validate_company(company)
    if args.command == "check":
        print(json.dumps({"verified": True, "company": company["name"], "company_id": COMPANY_ID, "portal": "https://jus001.freeagent.com"}))
    else:
        snapshot(fa, company, args.start, args.end, args.output)

if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
