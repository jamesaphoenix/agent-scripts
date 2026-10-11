#!/usr/bin/env python3
"""Collect public tutor profiles with cached pages and explicit locality evidence."""
import argparse
import csv
import datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

HOSTS = {"www.findtutors.co.uk", "tutorperch.com"}
AGENT = "TutorResearch/1.0"
BUCKS = {
    "amersham", "amersham on the hill", "aylesbury", "beaconsfield", "bierton", "buckingham",
    "burnham", "chalfont st giles", "chalfont st peter", "chalfont saint peter", "chesham",
    "chesham bois", "denham", "downley", "farnham common", "farnham royal", "gerrards cross",
    "great missenden", "haddenham", "hazlemere", "high wycombe", "hughenden", "iver", "iver heath",
    "little chalfont", "marlow", "marlow bottom", "prestwood", "princes risborough", "stoke mandeville",
    "stoke poges", "taplow", "wendover", "weston turville", "winslow", "wooburn", "wooburn green",
    "waddesdon", "penn", "tylers green", "olney", "newport pagnell", "milton keynes", "bancroft",
    "bletchley", "wolverton", "stony stratford", "great linford", "shenley brook end", "shenley church end",
    "sands", "wycombe marsh", "jordans", "george green", "new denham", "lower hartwell", "askett",
    "pitstone", "ivinghoe", "aston clinton", "great kingshill", "little kingshill", "longwick",
    "broughton buckinghamshire", "broughton (buckinghamshire)", "bishopstone (buckinghamshire)",
    "bourton", "bourton meadow", "bourne end buckinghamshire", "bourne end (buckinghamshire)",
}
NON_TUITION = re.compile(r"\b(?:yoga|football|fitness|personal training|swimming|pilates|driving)\b", re.I)
INSTITUTION = re.compile(r"\b(?:academy|university|college|grammar school|tuition centre|learning centre|kumon|mathnasium|explore learning)\b", re.I)
ACADEMIC = re.compile(r"math|english|science|biology|chemistry|physics|history|geography|language|french|spanish|german|italian|arabic|mandarin|chinese|japanese|russian|latin|greek|portuguese|hindi|urdu|economics|business|accounting|comput|programming|coding|philosophy|psychology|sociology|law|statistics|primary|11\+|11.plus|eleven.plus|gcse|a.level|sat|reading|writing|literacy|phonics|dyslexia|study skills|special needs|send|efl|esol|ielts|piano|violin|guitar|singing|music|drum|flute", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        temporary.chmod(0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    temporary.replace(path)


def admitted_url(value):
    try:
        p = urllib.parse.urlsplit(value)
        if p.scheme != "https" or p.hostname not in HOSTS or p.port not in (None, 443) or p.username or p.password:
            return None
        if p.hostname == "tutorperch.com":
            profile = bool(re.fullmatch(r"/t/[a-z0-9]+(?:-[a-z0-9]+)+/?", p.path))
            allowed = profile or p.path in ("/find/results", "/robots.txt")
        else:
            profile = bool(re.fullmatch(r"/(?:tutors-[^/]+|online-[^/]+)/[^/]+-\d+/?|/tutor/[a-z0-9-]+\.htm", p.path))
            allowed = profile or p.path == "/robots.txt" or bool(re.fullmatch(r"/[^/]*(?:tutors|lessons)[^/]*/", p.path))
        if not allowed:
            return None
        query = "" if profile else urllib.parse.urlencode(sorted((k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.startswith("utm_")))
        return urllib.parse.urlunsplit(("https", p.hostname, p.path, query, ""))
    except (ValueError, TypeError):
        return None


class Node:
    def __init__(self, tag="root", attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        return " ".join(" ".join(child.text() if isinstance(child, Node) else child for child in self.children).split())

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def first(self, predicate):
        return next((node for node in self.walk() if predicate(node)), None)


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def class_text(node, name):
    found = node.first(lambda n: n.has_class(name))
    return found.text() if found else ""


def locality_status(value):
    primary = value.split(",", 1)[0].strip().lower()
    if primary in BUCKS or primary in ("buckinghamshire", "bucks", "milton keynes (buckinghamshire)"):
        return "advertised_bucks_locality"
    if re.fullmatch(r"(?:[a-z -]+) \(buckinghamshire\)", primary):
        return "advertised_bucks_locality"
    return "unresolved" if not primary or primary in ("online", "online lessons") else "outside_or_unresolved"


def exclusion_reason(row):
    if row.get("institution") or INSTITUTION.search(row.get("name", "")):
        return "institution_or_chain"
    services = " ".join(row.get("services", []))
    if NON_TUITION.search(services) and not ACADEMIC.search(services):
        return "non_tuition_lessons"
    if not ACADEMIC.search(services):
        return "needs_subject_review"
    return None


def listing_records(text, url):
    document = Document(text).root
    rows = []
    for card in document.walk():
        if urllib.parse.urlsplit(url).hostname == "tutorperch.com" and card.attrs.get("data-slot") == "card":
            link = card.first(lambda n: n.tag == "a" and n.has_class("card-overlay-link"))
            location = card.first(lambda n: n.tag == "span" and n.first(lambda child: child.tag == "svg" and child.has_class("lucide-map-pin")) is not None)
            if link:
                source = admitted_url(urllib.parse.urljoin(url, link.attrs.get("href", "")))
                label = link.attrs.get("aria-label", "")
                if source and label.startswith("View ") and label.endswith("'s profile"):
                    subjects = [n.text() for n in card.walk() if n.tag == "li"]
                    rows.append({"name": label[5:-10], "platform": "tutorperch.com", "sourceUrls": [source],
                                 "listingUrl": url, "localityClaim": location.text() if location else "",
                                 "services": subjects, "personId": None, "evidenceKind": "directory_listing", "outreachEligible": False})
        if not card.has_class("itemv3"):
            continue
        source = admitted_url(urllib.parse.urljoin(url, card.attrs.get("data-link", "")))
        name = class_text(card, "username")
        if source and name:
            rows.append({"name": name, "platform": "findtutors.co.uk", "sourceUrls": [source],
                         "listingUrl": url, "localityClaim": class_text(card, "place"),
                         "services": [class_text(card, "subject")], "personId": None,
                         "evidenceKind": "directory_listing", "outreachEligible": False})
    return rows


def profile_record(text, url):
    url = admitted_url(url)
    if not url:
        return None
    p = urllib.parse.urlsplit(url)
    document = Document(text).root
    if p.hostname == "tutorperch.com":
        if not p.path.startswith("/t/"):
            return None
        for script in document.walk():
            if script.tag != "script" or script.attrs.get("type") != "application/ld+json":
                continue
            try:
                data = json.loads("".join(c for c in script.children if isinstance(c, str)))
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "ProfilePage":
                continue
            person = data.get("mainEntity", {})
            if not isinstance(person, dict) or person.get("@type") != "Person" or person.get("jobTitle") != "Tutor" or not isinstance(person.get("name"), str):
                continue
            area = person.get("areaServed") or {}
            return {"name": person["name"], "personId": p.path.rstrip("/").split("/")[-1],
                    "platform": "tutorperch.com", "sourceUrls": [url], "localityClaim": area.get("name", "") if isinstance(area, dict) else "",
                    "services": [v for v in person.get("knowsAbout", []) if isinstance(v, str)] if isinstance(person.get("knowsAbout"), list) else [], "evidenceKind": "public_person_profile",
                    "outreachEligible": False, "institution": False}
    else:
        section = document.first(lambda n: n.attrs.get("id") == "sectionaddetail")
        if not section:
            return None
        info = section.first(lambda n: n.has_class("info"))
        name = info.first(lambda n: n.has_class("user-name") or (n.tag == "span" and n.attrs.get("itemprop") == "name")) if info else None
        person = document.first(lambda n: n.attrs.get("id") == "iduser")
        report = document.first(lambda n: n.attrs.get("id") == "report_error")
        person_id = person.attrs.get("value") if person else report.attrs.get("data-u") if report else None
        if not name or not person_id or not person_id.isdigit():
            return None
        breadcrumb = section.first(lambda n: n.attrs.get("id") == "ariadna")
        crumbs = [n.text() for n in breadcrumb.walk() if n.tag == "span" and n.attrs.get("itemprop") == "name"] if breadcrumb else []
        academy = section.first(lambda n: n.attrs.get("id") == "logo_academy_link_profile")
        subject = section.first(lambda n: n.has_class("subject"))
        location = info.first(lambda n: n.attrs.get("id") == "line_location") if info else None
        return {"name": name.text(), "personId": person_id, "platform": "findtutors.co.uk", "sourceUrls": [url],
                "localityClaim": location.text() if location else crumbs[2] if len(crumbs) >= 3 else "", "services": [subject.text() if subject else ""],
                "evidenceKind": "public_advert_person", "outreachEligible": False,
                "institution": not academy or not academy.has_class("non-academy")}
    return None


def merge_profiles(rows):
    merged = {}
    for row in rows:
        key = (row["platform"], row.get("personId") or row["sourceUrls"][0])
        target = merged.setdefault(key, dict(row, services=[], sourceUrls=[]))
        for field in ("services", "sourceUrls"):
            target[field] = list(dict.fromkeys([*target[field], *row.get(field, [])]))
        if target.get("localityClaim") != row.get("localityClaim"):
            target["locationConflict"] = True
        target["institution"] = bool(target.get("institution") or row.get("institution"))
    return list(merged.values())


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        permitted = admitted_url(newurl)
        if not permitted:
            raise ValueError("Redirect left public directory paths")
        return super().redirect_request(req, fp, code, msg, headers, permitted)


class PageCache:
    def __init__(self, root, spacing=2):
        self.root, self.spacing, self.last_request = root, spacing, {}

    def key(self, url):
        return hashlib.sha256(url.encode()).hexdigest()

    def body_path(self, url):
        return self.root / "pages" / (self.key(url) + ".html")

    def download(self, url):
        host = urllib.parse.urlsplit(url).hostname
        delay = self.spacing - (time.monotonic() - self.last_request.get(host, 0))
        if delay > 0:
            time.sleep(delay)
        self.last_request[host] = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "text/html,text/plain"})
        with urllib.request.build_opener(RestrictedRedirect()).open(request, timeout=20) as response:
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("Page exceeded eight MB")
            return body, response.headers.get("Content-Type", ""), response.status

    def fetch(self, url):
        url = admitted_url(url)
        if not url:
            raise ValueError("URL is not a public directory page")
        receipt = self.root / "receipts" / (self.key(url) + ".json")
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if saved["url"] != url:
                raise ValueError("Cached URL changed")
            if saved.get("error"):
                return None
            body = self.body_path(url).read_bytes()
            if hashlib.sha256(body).hexdigest() != saved["sha256"]:
                raise ValueError("Cached page hash changed")
            return body.decode("utf-8", errors="replace")
        try:
            body, content_type, status = self.download(url)
            if "html" not in content_type and "text/plain" not in content_type:
                raise ValueError("Unsupported content type")
            path = self.body_path(url)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(body)
            path.chmod(0o600)
            save(receipt, {"url": url, "sha256": hashlib.sha256(body).hexdigest(), "status": status, "observedAt": now()})
            return body.decode("utf-8", errors="replace")
        except (OSError, ValueError) as error:
            save(receipt, {"url": url, "error": type(error).__name__, "observedAt": now()})
            return None


def linked_pages(text, url):
    links = []
    for node in Document(text).root.walk():
        value = node.attrs.get("href") if node.tag in ("a", "link") else None
        if value and (candidate := admitted_url(urllib.parse.urljoin(url, value))):
            links.append(candidate)
    return list(dict.fromkeys(links))


def local_directory_link(value, source):
    parsed, origin = urllib.parse.urlsplit(value), urllib.parse.urlsplit(source)
    if parsed.hostname != origin.hostname:
        return False
    if parsed.hostname == "tutorperch.com":
        origin_filters = dict(urllib.parse.parse_qsl(origin.query))
        filters = dict(urllib.parse.parse_qsl(parsed.query))
        return parsed.path == origin.path and filters.get("level") == origin_filters.get("level") and filters.get("mode") == origin_filters.get("mode")
    if parsed.path == origin.path:
        return True
    slug = parsed.path.lower().replace("-", " ")
    return "buckinghamshire" in slug or any(re.search(r"\b" + re.escape(town) + r"\b", slug) for town in BUCKS)


def export_csv(root, rows):
    path = root / "bucks-private-tutors.csv"
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as stream:
        temporary.chmod(0o600)
        writer = csv.DictWriter(stream, fieldnames=["name", "platform", "personId", "localityClaim", "services", "sourceUrls", "evidenceKind", "outreachEligible"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(r, services="; ".join(r["services"]), sourceUrls="; ".join(r["sourceUrls"])) for r in rows)
    temporary.replace(path)


def import_seed_batches(root, state):
    imported = state.setdefault("importedSeedBatches", [])
    rejected = state.setdefault("rejectedSeedBatches", [])
    for path in sorted((root / "seed-batches").glob("*.json")):
        if path.stem in imported or path.name in rejected:
            continue
        raw = path.read_bytes()
        if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != path.stem:
            rejected.append(path.name)
            raise ValueError("Seed batch hash changed or exceeded its bound")
        value = json.loads(raw)
        urls = value.get("urls") if isinstance(value, dict) else None
        if not isinstance(urls, list) or len(urls) > 5000:
            rejected.append(path.name)
            raise ValueError("Seed batch must contain bounded source URLs")
        for candidate in urls:
            url = admitted_url(candidate)
            if url and url not in state["seen"] and url not in state["queue"] and len(state["queue"]) < 20000:
                state["queue"].append(url)
        imported.append(path.stem)


def run(root, seeds, hours, page_limit, target):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "owner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job_path, state_path = root / "job.json", root / "state.json"
        if job_path.exists():
            job = json.loads(job_path.read_text())
        else:
            admitted = list(dict.fromkeys(admitted_url(s) for s in seeds if admitted_url(s)))
            if not admitted:
                raise ValueError("No permitted source seeds")
            job = {"startedAt": now(), "deadlineUnix": time.time() + hours * 3600, "pageLimit": page_limit,
                   "target": target, "seeds": admitted, "paidBudget": 0,
                   "scope": "Public individual tutor profiles across subjects in Bucks and Milton Keynes; exclude institutions. Locality is advertised tuition locality, not verified residence."}
            save(job_path, job)
        state = json.loads(state_path.read_text()) if state_path.exists() else {"queue": job["seeds"], "seen": [], "records": [], "attempts": 0, "errors": [], "phase": "running"}
        cache, robots = PageCache(root), {}
        while state["attempts"] < job["pageLimit"] and time.time() < job["deadlineUnix"]:
            try:
                import_seed_batches(root, state)
            except (OSError, ValueError, TypeError) as error:
                state["errors"].append({"reason": "seed_batch_rejected", "type": type(error).__name__})
            if not state["queue"]:
                break
            url = state["queue"].pop(0)
            if url in state["seen"]:
                continue
            state["seen"].append(url)
            host = urllib.parse.urlsplit(url).hostname
            if host not in robots:
                robot_text = cache.fetch("https://" + host + "/robots.txt")
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(robot_text.splitlines() if robot_text else ["User-agent: *", "Disallow: /"])
                robots[host] = parser
                cache.spacing = max(cache.spacing, parser.crawl_delay(AGENT) or 0)
            if not robots[host].can_fetch(AGENT, url):
                state["errors"].append({"url": url, "reason": "robots_disallowed"})
                save(state_path, state)
                continue
            state["attempts"] += 1
            try:
                text = cache.fetch(url)
                if text:
                    profile = profile_record(text, url)
                    if profile:
                        receipt_path = root / "receipts" / (cache.key(url) + ".json")
                        receipt = json.loads(receipt_path.read_text())
                        profile["pageEvidence"] = [{"url": url, "observedAt": receipt["observedAt"], "sha256": receipt["sha256"], "receipt": str(receipt_path)}]
                        state["records"].append(profile)
                    else:
                        cards = listing_records(text, url)
                        # Prefer actual local profiles, then pagination and linked local directories.
                        local_urls = [r["sourceUrls"][0] for r in cards if locality_status(r["localityClaim"]) == "advertised_bucks_locality" and not exclusion_reason(r)]
                        links = linked_pages(text, url)
                        links = [link for link in links if local_directory_link(link, url)]
                        if host == "www.findtutors.co.uk":
                            links = [link for link in links if "/tutor/" not in link and not re.search(r"-\d+/?$", urllib.parse.urlsplit(link).path)]
                        state["queue"].extend(link for link in [*local_urls, *links] if link not in state["seen"] and link not in state["queue"])
                        state["queue"] = state["queue"][:20000]
                else:
                    state["errors"].append({"url": url, "reason": "fetch_failed"})
            except (OSError, ValueError, KeyError, TypeError) as error:
                state["errors"].append({"url": url, "reason": type(error).__name__})
            profiles = merge_profiles(state["records"])
            qualified = [r for r in profiles if locality_status(r["localityClaim"]) == "advertised_bucks_locality" and not r.get("locationConflict") and not exclusion_reason(r)]
            save(root / "profiles.json", profiles)
            save(root / "bucks-private-tutors.json", qualified)
            export_csv(root, qualified)
            state["updatedAt"] = now()
            save(state_path, state)
            save(root / "status.json", {"observedAt": now(), "pid": os.getpid(), "phase": "running", "attempts": state["attempts"], "queue": len(state["queue"]),
                                        "namedProfiles": len(profiles), "bucksPrivateProfiles": len(qualified), "errors": len(state["errors"]), "paidCostUsd": 0})
            if len(qualified) >= job["target"]:
                break
        state["phase"] = "stage_complete"
        save(state_path, state)
        rows = json.loads((root / "bucks-private-tutors.json").read_text()) if (root / "bucks-private-tutors.json").exists() else []
        export_csv(root, rows)
        save(root / "status.json", {"observedAt": now(), "pid": os.getpid(), "phase": state["phase"], "attempts": state["attempts"], "queue": len(state["queue"]),
                                    "bucksPrivateProfiles": len(rows), "errors": len(state["errors"]), "paidCostUsd": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--seed", action="append", default=[])
    parser.add_argument("--hours", type=float, default=3)
    parser.add_argument("--pages", type=int, default=5000)
    parser.add_argument("--target", type=int, default=1000)
    args = parser.parse_args()
    if not 0 < args.hours <= 6 or not 1 <= args.pages <= 10000 or not 1 <= args.target <= 5000:
        parser.error("Choose a bounded stage: up to six hours and ten thousand pages")
    run(args.run_dir, args.seed, args.hours, args.pages, args.target)
