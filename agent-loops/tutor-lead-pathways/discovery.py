"""Finite, cached tutor research. Provider receipts are the cost authority."""
import base64
import collections
import csv
import datetime
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

UTC = datetime.timezone.utc
ENDPOINTS = {
    "maps": "/v3/serp/google/maps/live/advanced",
    "organic": "/v3/serp/google/organic/live/advanced",
    "directories": "/v3/serp/google/organic/live/advanced",
    "business": "/v3/business_data/business_listings/search/live",
}
CHAINS = re.compile(r"\b(kumon|explore learning|first class learning|mathnasium|tutor doctor|teachers to your home|bright young things|kip\s*mcgrath|number\s*works\s*['’]?\s*n\s*words)\b", re.I)
NON_ACADEMIC_CATEGORIES = {
    "museum", "driving school", "martial arts school", "piano instructor",
    "art school", "life coach", "translation service",
}
SOURCE_HOSTS = {
    "11plusguide.com", "elevenplusexams.co.uk", "hometutorsdirectory.co.uk", "my-towns.co.uk",
    "redkitedays.co.uk", "tophelp.co.uk", "universitytutor.com", "teachingpersonnel.com",
    "studysmarter.co.uk", "womenforhire.com", "tutorful.co.uk", "superprof.co.uk", "tutorhunt.com",
    "firsttutors.com", "bdadyslexia.org.uk", "haddenham.net", "chilternchatter.com",
    "directory.familyinfo.buckinghamshire.gov.uk", "findtutors.co.uk", "uk.indeed.com",
    "childcare.co.uk", "bark.com", "yell.com", "schoolguide.co.uk", "cylex-uk.co.uk",
    "yelp.com", "yelp.co.uk", "yelp.ca",
}
PROFILE_PATHS = {"childcare.co.uk": re.compile(r"^/profile/\d+/?$")}
MERGED_LISTS = ("aliases", "placeIds", "provenance", "emails", "identifiers", "siteEvidence", "establishments", "contactClaims")


def now_iso():
    return datetime.datetime.now(UTC).isoformat()


def parse_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def request_key(lane, payload):
    endpoint = ENDPOINTS.get(lane, lane)
    clean = {k: v for k, v in payload.items() if k != "tag"}
    return hashlib.sha256((endpoint + "\n" + json.dumps(clean, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def validate_plan(root, job):
    if file_hash(root / "plan.json") != job["planSha256"]:
        raise ValueError("Immutable discovery plan changed")


class PaidCache:
    def __init__(self, root, budget_cents, prior_cents):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not 100 <= budget_cents <= 2000 or prior_cents < 0 or budget_cents + prior_cents > 10000:
            raise ValueError("Shared USD100 allowance exceeded")
        self.db = sqlite3.connect(self.root / "paid.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, budget INTEGER, prior INTEGER);
            CREATE TABLE IF NOT EXISTS requests (
                key TEXT PRIMARY KEY, lane TEXT, payload TEXT, status TEXT, reservation INTEGER,
                cost_cents INTEGER DEFAULT 0, cost_usd TEXT DEFAULT '0', task_id TEXT,
                error TEXT, receipt TEXT, receipt_hash TEXT, imported INTEGER DEFAULT 0,
                created_at TEXT, updated_at TEXT);
        """)
        original = self.db.execute("SELECT budget,prior FROM settings WHERE id=1").fetchone()
        if original and (original["budget"], original["prior"]) != (budget_cents, prior_cents):
            self.db.close()
            raise ValueError("Original budget is immutable")
        self.db.execute("INSERT OR IGNORE INTO settings VALUES (1,?,?)", (budget_cents, prior_cents))
        self.db.commit()
        self.budget_cents, self.prior_cents = budget_cents, prior_cents

    def usage(self):
        rows = self.db.execute("SELECT * FROM requests WHERE imported=0").fetchall()
        spent = sum(r["cost_cents"] for r in rows)
        reserved = sum(r["reservation"] for r in rows if r["status"] == "submitting")
        uncertain = sum(r["reservation"] for r in rows if r["status"] == "uncertain")
        return {"reportedCostUsd": float(sum(Decimal(r["cost_usd"]) for r in rows)),
                "spentCents": spent, "reservedCents": reserved, "uncertainCents": uncertain,
                "exposureCents": spent + reserved + uncertain,
                "remainingCents": self.budget_cents - spent - reserved - uncertain,
                "priorExposureCents": self.prior_cents,
                "sharedExposureCents": self.prior_cents + spent + reserved + uncertain,
                "succeeded": sum(r["status"] == "succeeded" for r in rows),
                "failed": sum(r["status"] == "failed" for r in rows),
                "importedReceipts": self.db.execute("SELECT count(*) FROM requests WHERE imported=1").fetchone()[0]}

    def get(self, key):
        row = self.db.execute("SELECT * FROM requests WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def reserve(self, key, lane, payload, maximum_cents):
        if not 1 <= maximum_cents <= 200:
            raise ValueError("Invalid per-request reserve")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if self.get(key) or self.usage()["remainingCents"] < maximum_cents:
                self.db.rollback()
                return False
            self.db.execute("INSERT INTO requests(key,lane,payload,status,reservation,created_at,updated_at) VALUES(?,?,?,'submitting',?,?,?)",
                            (key, lane, json.dumps(payload, sort_keys=True), maximum_cents, now_iso(), now_iso()))
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def mark_uncertain(self, key, message):
        self.db.execute("UPDATE requests SET status='uncertain',error=?,updated_at=? WHERE key=? AND status='submitting'",
                        (message[:250], now_iso(), key))
        self.db.commit()

    def settle(self, key, envelope):
        row = self.get(key)
        if not row or row["status"] not in ("submitting", "uncertain"):
            raise ValueError("Request is not unsettled")
        try:
            cost = Decimal(str(envelope["cost"]))
            tasks = envelope["tasks"]
            if len(tasks) != 1 or not tasks[0].get("id"):
                raise ValueError("Missing paid task identity")
            task = tasks[0]
            if not cost.is_finite() or cost < 0 or cost > 10000 or Decimal(str(task["cost"])) != cost:
                raise ValueError("Paid task cost mismatch")
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("Paid cost cannot be verified") from error
        receipt = self.root / "receipts" / (key + ".json")
        atomic_json(receipt, {"key": key, "lane": row["lane"], "payload": json.loads(row["payload"]), "response": envelope})
        status = "succeeded" if envelope.get("status_code") == 20000 and task.get("status_code") in (20000, 40106) else "failed"
        message = None if status == "succeeded" else f"provider {envelope.get('status_code')}/{task.get('status_code')}: {task.get('status_message', '')[:160]}"
        self.db.execute("UPDATE requests SET status=?,cost_cents=?,cost_usd=?,reservation=0,task_id=?,error=?,receipt=?,receipt_hash=?,updated_at=? WHERE key=?",
                        (status, int((cost * 100).to_integral_value(rounding=ROUND_CEILING)), str(cost), task["id"], message,
                         str(receipt), file_hash(receipt), now_iso(), key))
        self.db.commit()

    def recover_interrupted(self):
        for row in self.db.execute("SELECT * FROM requests WHERE status='submitting'").fetchall():
            receipt = self.root / "receipts" / (row["key"] + ".json")
            if receipt.exists():
                saved = json.loads(receipt.read_text())
                if saved["key"] != row["key"] or saved["payload"] != json.loads(row["payload"]):
                    raise ValueError("Interrupted receipt identity mismatch")
                self.settle(row["key"], saved["response"])
            else:
                self.mark_uncertain(row["key"], "Interrupted after durable reservation; never resubmit")

    def import_receipt(self, lane, payload, envelope):
        key = request_key(lane, payload)
        if self.get(key):
            return
        tasks = envelope.get("tasks") or []
        if len(tasks) != 1 or not tasks[0].get("id"):
            return
        task = tasks[0]
        if envelope.get("cost") is None or task.get("cost") != envelope["cost"]:
            raise ValueError("Historical receipt has unverifiable charge")
        receipt = self.root / "receipts" / (key + ".json")
        atomic_json(receipt, {"key": key, "lane": lane, "payload": payload, "response": envelope, "historical": True})
        status = "succeeded" if envelope.get("status_code") == 20000 and task.get("status_code") in (20000, 40106) else "failed"
        self.db.execute("INSERT INTO requests(key,lane,payload,status,reservation,task_id,receipt,receipt_hash,imported,created_at,updated_at) VALUES(?,?,?,?,0,?,?,?,1,?,?)",
                        (key, lane, json.dumps(payload, sort_keys=True), status, task["id"], str(receipt), file_hash(receipt), now_iso(), now_iso()))
        self.db.commit()

    def receipt(self, row):
        path = Path(row["receipt"])
        if file_hash(path) != row["receipt_hash"]:
            raise ValueError("Archived receipt changed")
        return json.loads(path.read_text())["response"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provider_request(cache, lane, payload, maximum_cents=100):
    key = request_key(lane, payload)
    saved = cache.get(key)
    if saved:
        return cache.receipt(saved) if saved["receipt"] else None
    if not cache.reserve(key, lane, payload, maximum_cents):
        return None
    login, password = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if not login or not password:
        # No network call occurred; still hold the identity rather than accidentally replay it.
        cache.mark_uncertain(key, "Credentials unavailable before submission")
        raise ValueError("DataForSEO runtime credentials unavailable")
    auth = base64.b64encode((login + ":" + password).encode()).decode()
    request_payload = dict(payload, tag="bucks-pathways-" + key[:24])
    request = urllib.request.Request("https://api.dataforseo.com" + ENDPOINTS[lane],
                                     json.dumps([request_payload]).encode(),
                                     {"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=100) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("Paid receipt exceeded 16 MB")
        envelope = json.loads(raw)
        tasks = envelope.get("tasks") or []
        if len(tasks) != 1 or tasks[0].get("data", {}).get("tag") != request_payload["tag"]:
            raise ValueError("Paid receipt request identity mismatch")
        cache.settle(key, envelope)
        return envelope
    except (OSError, ValueError, urllib.error.URLError) as error:
        cache.mark_uncertain(key, type(error).__name__ + ": paid response could not be verified")
        return None


def public_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    except ValueError:
        return None


def website_key(value):
    value = public_url(value)
    if not value:
        return None
    p = urllib.parse.urlsplit(value)
    host = p.hostname.lower().removeprefix("www.")
    if source_host(host):
        # Individual profiles and directory pages cannot share a business identity.
        return host + (p.path.rstrip("/") or "/")
    if host in ("facebook.com", "instagram.com", "linktr.ee") or host.endswith(".wixsite.com"):
        return host + "/" + p.path.strip("/").split("/")[0]
    # A tenant subdomain remains part of its identity.
    return host


def source_host(host):
    return any(host == known or host.endswith("." + known) for known in SOURCE_HOSTS)


def professional_profile(value):
    value = public_url(value)
    if not value:
        return False
    parsed = urllib.parse.urlsplit(value)
    pattern = PROFILE_PATHS.get(parsed.hostname.lower().removeprefix("www."))
    return bool(pattern and pattern.fullmatch(parsed.path))


def phone_key(value):
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("0044"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = "44" + digits[1:]
    return digits if len(digits) >= 10 else None


def hold_reason(record):
    name = record.get("name") or ""
    url = record.get("website") or record.get("sourceUrl") or ""
    host = urllib.parse.urlsplit(url).hostname or ""
    if CHAINS.search(name + " " + host.replace(".", " ")):
        return "recognised_chain"
    if source_host(host) and not professional_profile(url):
        return "source_directory_or_job_page"
    if (record.get("category") or "").strip().lower() in NON_ACADEMIC_CATEGORIES:
        return "outside_academic_tuition_scope"
    if record.get("category") in ("Primary school", "Secondary school"):
        return "school"
    if host.endswith((".sch.uk", ".ac.uk")) or re.search(r"(?i)\b(?:grammar|high|secondary|primary) school\b", name) and not re.search(r"(?i)tutor|tuition", name):
        return "school_or_college"
    if re.search(r"(?i)\b(tutor job|tutor vacancy|tutoring jobs)\b", name):
        return "job_page"
    return None


def business_record(item, source):
    cid = str(item["cid"]) if item.get("cid") else None
    contacts = [contact for contact in (item.get("contact_info") or []) if isinstance(contact, dict)]
    phone = item.get("phone") or next((c.get("value") for c in contacts if c.get("type") == "telephone"), None)
    if isinstance(phone, dict):
        phone = phone.get("value") or phone.get("phone")
    record = {"name": (item.get("title") or item.get("name") or "Unnamed listing").replace("\u2014", "-"),
              "website": public_url(item.get("url") or item.get("website")), "placeId": item.get("place_id"),
              "cid": cid, "phone": phone, "advertisedAddress": item.get("address"), "category": item.get("category"),
              "latitude": item.get("latitude"), "longitude": item.get("longitude"),
              "sourceUrl": "https://www.google.com/maps?cid=" + cid if cid else public_url(item.get("contact_url")),
              "provenance": [source], "reviewed": False, "localVerified": False, "outreachEligible": False,
              "independentVerified": False, "elevenPlusVerified": False, "status": "research", "emails": [], "contactClaims": []}
    record["websiteKind"] = "professional_profile" if professional_profile(record["website"]) else "website" if record["website"] else "none"
    for contact in contacts:
        value = contact.get("value")
        if contact.get("type") == "mail":
            emails = clean_emails([value])
            if not emails:
                continue
            value = emails[0]
        elif contact.get("type") != "telephone" or not phone_key(value):
            continue
        record["contactClaims"].append({"type": contact["type"], "value": value, "providerSource": contact.get("source"),
                                        "sourceUrl": record["sourceUrl"], "verified": False})
    record["holdReason"] = hold_reason(record)
    if record["holdReason"]:
        record["status"] = "held"
    return record


def receipt_records(envelope, source):
    out = []
    for task in envelope.get("tasks") or []:
        if task.get("status_code") not in (20000, 40106):
            continue
        for result in task.get("result") or []:
            for index, item in enumerate(result.get("items") or []):
                provenance = dict(source, taskId=task["id"], itemIndex=index + 1)
                if item.get("type") in ("maps_search", "business_listing"):
                    out.append(business_record(item, provenance))
                elif item.get("type") == "organic" and public_url(item.get("url")):
                    record = business_record(item, provenance)
                    record["sourceUrl"] = public_url(item["url"])
                    record["description"] = (item.get("description") or "").replace("\u2014", "-")
                    record["sourceOnly"] = bool(hold_reason(record)) or source.get("lane") == "directories" and not professional_profile(record["website"])
                    out.append(record)
    return out


def merge_businesses(records):
    # Union by observed business identifiers, preserving every establishment and provenance.
    groups, keys = [], {}
    for original in records:
        record = dict(original)
        identifiers = []
        for prefix, value in [("place", record.get("placeId")), ("cid", record.get("cid")),
                              ("phone", phone_key(record.get("phone"))), ("website", website_key(record.get("website")))]:
            if value:
                identifiers.append(prefix + ":" + str(value))
        if not identifiers:
            identifiers.append("fallback:" + hashlib.sha256(json.dumps([record.get("name"), record.get("advertisedAddress"), record.get("sourceUrl")]).encode()).hexdigest())
        matches = {keys[k] for k in identifiers if k in keys}
        index = min(matches) if matches else len(groups)
        if not matches:
            groups.append({**record, **{field: [] for field in MERGED_LISTS}})
        target = groups[index]
        target["sourceOnly"] = bool(target.get("sourceOnly")) and bool(record.get("sourceOnly"))
        if record.get("category") or record.get("placeId"):
            target["name"] = record["name"]
        for other in sorted(matches - {index}):
            old = groups[other]
            if old is None:
                continue
            for field in MERGED_LISTS:
                target[field].extend(x for x in old[field] if x not in target[field])
            for key in old["identifiers"]:
                keys[key] = index
            target["sourceOnly"] = target["sourceOnly"] and bool(old.get("sourceOnly"))
            groups[other] = None
        target["identifiers"].extend(k for k in identifiers if k not in target["identifiers"])
        for key in identifiers:
            keys[key] = index
        for field in ["website", "phone", "advertisedAddress", "category", "sourceUrl"]:
            if not target.get(field) and record.get(field):
                target[field] = record[field]
        if record.get("name") and record["name"] not in target["aliases"]:
            target["aliases"].append(record["name"])
        if record.get("placeId") and record["placeId"] not in target["placeIds"]:
            target["placeIds"].append(record["placeId"])
        if record.get("placeId") or record.get("cid"):
            establishment = {key: record.get(key) for key in ["placeId", "cid", "advertisedAddress", "phone", "latitude", "longitude"]}
            if establishment not in target["establishments"]:
                target["establishments"].append(establishment)
        for field in ["provenance", "emails", "siteEvidence", "contactClaims"]:
            target[field].extend(x for x in record.get(field, []) if x not in target[field])
        if record.get("holdReason"):
            target["holdReason"], target["status"] = record["holdReason"], "held"
    return [g for g in groups if g is not None]


def collect_records(cache, run_dir):
    records = []
    for row in cache.db.execute("SELECT * FROM requests WHERE receipt IS NOT NULL AND status='succeeded'").fetchall():
        payload = json.loads(row["payload"])
        records.extend(receipt_records(cache.receipt(row), {"lane": row["lane"], "key": row["key"],
                       "keyword": payload.get("keyword"), "historical": bool(row["imported"]), "observedAt": row["updated_at"]}))
    for path in sorted((run_dir / "sdk/results").glob("*.json")):
        result = json.loads(path.read_text())
        for lead in result.get("leads", []):
            urls = [url for value in lead.get("sourceUrls", []) if (url := public_url(value))]
            if not urls:
                continue
            record = business_record({"title": lead["name"], "website": lead.get("website"), "phone": lead.get("phone")},
                                     {"lane": "codex_oauth", "turn": path.name, "sourceUrls": urls, "notes": lead.get("notes"),
                                      "localityClaim": lead.get("localityClaim"), "servicesClaim": lead.get("services")})
            record["sourceUrl"] = urls[0]
            # Agent-reported emails remain claims until seen on a fetched business page.
            record["emailClaim"] = lead.get("email")
            for email in clean_emails([lead.get("email")]):
                record["contactClaims"].append({"type": "mail", "value": email, "providerSource": "codex_oauth", "sourceUrl": urls[0], "verified": False})
            records.append(record)
    # Reuse prior Go page evidence, then add this run's fetched pages. No website refetch is required.
    manifest = json.loads((run_dir / "manifest.json").read_text()) if (run_dir / "manifest.json").exists() else {}
    historical = list(Path(manifest["historyRoot"]).glob("tracelearn-tutors-*/discovery.json")) if manifest.get("historyRoot") else []
    for path in historical + sorted((run_dir / "crawls").glob("*/crawl.json")):
        saved = json.loads(path.read_text())
        crawl = saved.get("crawl", saved)
        for result in crawl.get("results", []):
            evidence = result.get("evidence")
            if not evidence or result.get("error"):
                continue
            text = evidence.get("text", "")
            if not re.search(r"(?i)\b(tutor|tutoring|tuition|11.plus|eleven.plus|primary)\b", text):
                continue
            url = public_url(evidence.get("url") or result["node"]["url"])
            if not url:
                continue
            emails = clean_emails(evidence.get("emails", []))
            record = business_record({"title": evidence.get("title") or "Research website", "url": url},
                                     {"lane": "go_crawler", "sourceUrl": url, "checkpoint": str(path),
                                      "historical": path in historical, "observedAt": result.get("fetchedAt")})
            record["sourceUrl"] = url
            record["emails"] = [{"email": email, "sourceUrl": url, "verified": False, "extraction": "go_page_evidence"} for email in emails]
            record["siteEvidence"] = [{"url": url, "title": evidence.get("title"), "elevenPlusHint": bool(evidence.get("elevenPlus")),
                                       "primaryHint": bool(evidence.get("primary")), "independentHint": bool(evidence.get("independentHint")),
                                       "postcodes": evidence.get("postcodes", []), "contentHash": evidence.get("contentHash")}]
            records.append(record)
    return records


def clean_emails(values):
    candidates = sorted({value.strip().lower() for value in values if isinstance(value, str) and
                         re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}", value.strip())})
    out = []
    for email in candidates:
        domain = email.split("@", 1)[1]
        if domain in ("learnworlds.com", "wix.com", "squarespace.com", "example.com", "sentry.io", "mysite.com", "edublink.com"):
            continue
        malformed = any(email != other and email.endswith(other) and
                        (email[:-len(other)].isdigit() or email[:-len(other)] in ("email", "e-mail", "tel", "phone"))
                        for other in candidates)
        if not malformed:
            out.append(email)
    return out


def write_exports(run_dir, cache, final=False):
    records = collect_records(cache, run_dir)
    merged = merge_businesses(records)
    for record in merged:
        record["holdReason"] = hold_reason(record) or record.get("holdReason")
        if record["holdReason"]:
            record["status"] = "held"
        record["email"] = record["emails"][0]["email"] if record["emails"] else None
        record["emailsUnverified"] = [item["email"] for item in record["emails"]]
        record["newInThisRun"] = not any(source.get("historical") for source in record["provenance"])
        # No heuristic promotes consent, locality, ownership or outreach eligibility.
        record["reviewed"] = record["localVerified"] = record["independentVerified"] = record["elevenPlusVerified"] = record["outreachEligible"] = False
        record["subjectEvidence"] = "11_plus_hint" if any(e["elevenPlusHint"] for e in record["siteEvidence"]) else "primary_hint" if any(e["primaryHint"] for e in record["siteEvidence"]) else "needs_subject_verification"
    output = run_dir / "exports"
    atomic_json(output / "businesses.json", merged)
    output.mkdir(exist_ok=True)
    index = run_dir / "sdk/known-leads.tsv"
    index.parent.mkdir(exist_ok=True, mode=0o700)
    with index.with_suffix(".tsv.tmp").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["name", "website", "phone", "advertisedAddress"],
                                delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    index.with_suffix(".tsv.tmp").replace(index)
    fields = ["name", "website", "phone", "advertisedAddress", "category", "status", "holdReason", "email", "reviewed", "localVerified", "independentVerified", "elevenPlusVerified", "outreachEligible", "sourceUrl"]
    temporary = output / "businesses.csv.tmp"
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    os.replace(temporary, output / "businesses.csv")
    with (output / "tutors-research.csv.tmp").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields + ["subjectEvidence"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(row for row in merged if row.get("status") == "research" and not row.get("sourceOnly"))
    os.replace(output / "tutors-research.csv.tmp", output / "tutors-research.csv")
    summary = {"observedAt": now_iso(), "final": final, "sourceOccurrences": len(records), "businessIdentities": len(merged),
               "research": sum(r.get("status") == "research" for r in merged), "held": sum(r.get("status") == "held" for r in merged),
               "withoutWebsite": sum(not r.get("website") for r in merged), "withExtractedUnverifiedEmail": sum(bool(r.get("emails")) for r in merged), "budget": cache.usage(),
               "newResearchIdentities": sum(r["newInThisRun"] and r.get("status") == "research" for r in merged),
               "withProviderEmailClaims": sum(any(c.get("type") == "mail" for c in r.get("contactClaims", [])) for r in merged),
               "newCrawlerEvidencePages": sum(any(s.get("lane") == "go_crawler" and not s.get("historical") for s in r.get("provenance", [])) for r in records),
               "qualification": "Research only; independent status, county location, subject and outreach eligibility require verification."}
    atomic_json(output / "summary.json", summary)
    return summary
