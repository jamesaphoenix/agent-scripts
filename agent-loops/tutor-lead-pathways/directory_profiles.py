#!/usr/bin/env python3
"""Collect public tutor profiles with cached pages and explicit locality evidence."""
import argparse
import csv
import datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from discovery import tls_context
from configuration import loaded_source_sha
from lead_scope import load_scope, query_priority, services_text

HOSTS = {"www.findtutors.co.uk", "tutorperch.com", "www.childcare.co.uk", "tutordex.net", "tutorextra.co.uk", "www.uktutors.com", "www.tutorhunt.com", "www.tuitionlist.co.uk", "thetutorsassociation.wildapricot.org"}
AGENT = "TutorResearch/1.0"
BUCKS = {
    "amersham", "amersham on the hill", "aylesbury", "beaconsfield", "bierton", "buckingham",
    "burnham", "chalfont st giles", "chalfont st peter", "chalfont saint peter", "chesham",
    "chesham bois", "denham", "downley", "farnham common", "farnham royal", "gerrards cross",
    "great missenden", "haddenham", "hazlemere", "high wycombe", "hughenden", "iver", "iver heath",
    "little chalfont", "chalfont saint giles", "marlow", "marlow bottom", "prestwood", "princes risborough", "stoke mandeville",
    "stoke poges", "taplow", "wendover", "weston turville", "winslow", "wooburn", "wooburn green",
    "waddesdon", "penn", "tylers green", "olney", "newport pagnell", "milton keynes", "bancroft",
    "bletchley", "wolverton", "stony stratford", "great linford", "shenley brook end", "shenley church end",
    "sands", "wycombe marsh", "jordans", "george green", "new denham", "lower hartwell", "askett",
    "pitstone", "ivinghoe", "aston clinton", "great kingshill", "little kingshill", "longwick",
    "broughton buckinghamshire", "broughton (buckinghamshire)", "bishopstone (buckinghamshire)",
    "bourton", "bourton meadow", "bourne end buckinghamshire", "bourne end (buckinghamshire)",
    "wavendon", "upper weald", "holmer green",
    "flackwell heath", "bledlow ridge", "hughenden valley", "little kimble", "long crendon",
    "stewkley", "stokenchurch", "woburn sands",
    "cheddington", "far bletchley", "new bradwell", "bow brickhill", "little marlow",
    "hodge lea", "fishermead", "newton leys", "walnut tree", "kents hill", "great holm", "fullers slade",
    "wavendon gate", "tattenhoe", "hanslope", "lavendon", "emberton", "north crawley", "shenley lodge",
    "edlesborough", "wingrave", "seer green", "weedon", "west wycombe", "naphill", "turville", "medmenham",
    "little horwood", "great horwood", "soulbury", "stoke hammond", "steeple claydon", "calvert", "gawcott",
    "haversham", "grange farm", "tylers green",
}
CHILDCARE_DISTRICTS = {"aylesbury vale", "chiltern", "south bucks", "wycombe"}
CHILDCARE_DIRECTORY_PATH = re.compile(r'/search/Private-Tutors/Buckinghamshire(?:/(?:11-Plus|Maths-Tutor|English-Tutor|Primary-up-to-11-years))?(?:/[1-9]\d{0,2})?')
NON_TUITION = re.compile(r"\b(?:yoga|football|fitness|personal training|swimming|pilates|driving)\b", re.I)
INSTITUTION = re.compile(r"\b(?:academy|university|college|grammar school|tuition centre|learning centre|kumon|mathnasium|explore learning)\b", re.I)
ACADEMIC = re.compile(r"math|english|science|biology|chemistry|physics|history|geography|language|french|spanish|german|italian|arabic|mandarin|chinese|japanese|russian|latin|greek|portuguese|hindi|urdu|economics|business|accounting|comput|programming|coding|philosophy|psychology|sociology|law|statistics|primary|11\+|11.plus|eleven.plus|gcse|a.level|sat|reading|writing|literacy|phonics|dyslexia|study skills|special needs|send|efl|esol|ielts|piano|violin|guitar|singing|music|drum|flute|\b(?:art|anatomy|theology|polish|after[ -]school tuition|secondary school)\b", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def decode_page(body, content_type=""):
    declared = re.search(r"charset\s*=\s*[\"']?([a-z0-9._-]+)", content_type, re.I)
    if not declared:
        declared = re.search(r"<meta\b[^>]*charset\s*=\s*[\"']?([a-z0-9._-]+)", body[:4096].decode("ascii", errors="ignore"), re.I)
    encoding = declared.group(1).casefold() if declared else "utf-8"
    # HTML treats these legacy labels as Windows-1252, including smart punctuation.
    if encoding in {"iso-8859-1", "iso8859-1", "latin1", "windows-1252", "cp1252"}:
        encoding = "cp1252"
    else:
        encoding = "utf-8"
    return body.decode(encoding, errors="replace")


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
        if p.hostname == 'thetutorsassociation.wildapricot.org':
            from tta_scraper import PROFILE_PATH
            profile = bool(PROFILE_PATH.fullmatch(p.path))
            allowed = profile or p.path == '/robots.txt'
        elif p.hostname == "www.tutorhunt.com":
            profile = bool(re.fullmatch(r"/users/[a-z0-9-]{1,64}/", p.path))
            allowed = profile or p.path == "/robots.txt" or bool(re.fullmatch(r"/(?:subjects/[a-z0-9-]+/)?locations/[a-z0-9-]+/", p.path))
        elif p.hostname == "www.uktutors.com":
            profile = bool(re.fullmatch(r"/tutor/\d+/[a-z0-9-]+", p.path))
            allowed = profile or p.path in ("/robots.txt", "/county/buckinghamshire-tutors/") or bool(re.fullmatch(r"/town/[a-z0-9-]+-tutors/", p.path))
        elif p.hostname == "www.tuitionlist.co.uk":
            profile = bool(re.fullmatch(r"/tutor/[a-z0-9-]+-[a-f0-9]{8}", p.path))
            allowed = profile or p.path in ("/robots.txt", "/find-a-tutor")
        elif p.hostname == "tutordex.net":
            profile = bool(re.fullmatch(r"/tutor/\d+/[a-z0-9-]+", p.path))
            allowed = profile or p.path == "/robots.txt" or bool(re.fullmatch(r"/uk/academic/town/[a-z0-9-]+(?:/[a-z0-9-]+){0,2}", p.path))
        elif p.hostname == "tutorextra.co.uk":
            profile = bool(re.fullmatch(r"/profile/[a-z0-9-]+/\d+-\d+", p.path))
            allowed = profile or p.path == "/robots.txt" or bool(re.fullmatch(r"/search/[a-z0-9-]+-lessons-in/[a-z0-9-]+", p.path))
        elif p.hostname == "www.childcare.co.uk":
            profile = bool(re.fullmatch(r"/profile/\d+", p.path))
            allowed = profile or p.path == "/robots.txt" or bool(CHILDCARE_DIRECTORY_PATH.fullmatch(p.path))
        elif p.hostname == "tutorperch.com":
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


def parent_node(document, node):
    return next((n for n in document.walk() if any(child is node for child in n.children)), None)


def named_section(document, heading):
    matches = [n for n in document.walk() if n.tag == 'h2' and n.text() == heading]
    return parent_node(document, matches[0]) if len(matches) == 1 else None


def locality_status(value):
    primary = ' '.join(value.split(",", 1)[0].casefold().split())
    primary = re.sub(r'\bst\.(?=\s)', 'st', primary)
    if primary in BUCKS or primary in ("buckinghamshire", "bucks", "milton keynes (buckinghamshire)"):
        return "advertised_bucks_locality"
    # Some own headers omit the comma or qualify a town with Milton Keynes.
    # Recognise the same known local town, never a later nearby place in a list.
    qualified = re.fullmatch(r'(.+?)\s+(?:\((buckinghamshire|bucks|milton keynes)\)|'
                             r'(buckinghamshire|bucks|milton keynes))', primary)
    if qualified and qualified[1] in BUCKS:
        return "advertised_bucks_locality"
    if re.fullmatch(r"(?:[a-z -]+) \(buckinghamshire\)", primary):
        return "advertised_bucks_locality"
    return "unresolved" if not primary or primary in ("online", "online lessons") else "outside_or_unresolved"


def profile_locality_status(row):
    if row.get('platform') == 'thetutorsassociation.wildapricot.org' and row.get('evidenceKind') == 'public_person_profile':
        from tta_scraper import OUTSIDE_BUCKS_TOWNS
        if row.get('profileCity', '').split(',', 1)[0].strip().casefold() in OUTSIDE_BUCKS_TOWNS:
            return 'outside_or_unresolved'
        county = row.get('profileCounty', '').strip().casefold()
        if county and county not in ('england', 'uk', 'united kingdom', 'great britain'):
            return 'advertised_bucks_locality' if county in ('bucks', 'buckinghamshire', 'milton keynes') else 'outside_or_unresolved'
        return locality_status(re.sub(r'\bSt\.', 'St', row.get('profileCity', ''), flags=re.I))
    # These are the former Bucks district names used in Childcare's tutor header.
    # Its county breadcrumb can contradict that header and never supplies proof.
    if row.get("platform") == "childcare.co.uk" and row.get("evidenceKind") == "public_person_profile" and row.get("localityClaim", "").strip().lower() in CHILDCARE_DISTRICTS:
        return "advertised_bucks_locality"
    return locality_status(row.get("localityClaim", ""))


def exclusion_reason(row):
    if row.get("advertisedUnavailable"):
        return "profile_advertises_unavailable"
    if row.get("institution") or INSTITUTION.search(row.get("name", "")):
        return "institution_or_chain"
    if row.get("nonTuitionAdvert"):
        return "non_tuition_advert"
    if row.get("hasQualifyingTutorAdvert") is False:
        return "non_tuition_or_unreviewed_adverts"
    services = " ".join(row.get("services", []))
    if NON_TUITION.search(services) and not ACADEMIC.search(services):
        return "non_tuition_lessons"
    if not ACADEMIC.search(services) and not re.search(r'\b(?:KS[12]|key stage [12])\b', services, re.I):
        return "needs_subject_review"
    return None


def public_person(platform, person_id, name, location, services, url):
    return {"name": name, "personId": person_id, "platform": platform, "sourceUrls": [url],
            "localityClaim": location, "services": services, "evidenceKind": "public_person_profile",
            "outreachEligible": False, "institution": False}


def annotate_advert(row, title="", description=""):
    non_tuition = r"\b(?:anxiety coaching|life coaching|therapy|psychotherapy|counselling|personal training|fitness|yoga|pilates|swimming|driving)\b"
    teaching = r"\b(?:tutor|tuition|lessons?|teaching|teacher|gcse|a[ -]level|exams?)\b|11\+"
    row["nonTuitionAdvert"] = bool(re.search(non_tuition, title, re.I) and not re.search(teaching, title, re.I))
    personal = r"\bI(?:\s+am|'m|’m)\s+(?:currently\s+)?(?:resident|based|living|located|residing)\s+(?:in|at)\s+|\bI\s+(?:currently\s+)?(?:live|reside)\s+in\s+"
    implicit = r"\bcurrently\s+(?:resident|based|living|located|residing)\s+(?:(?:in|at)\s+)?"
    declarations = []
    for text, cue in [(title, implicit), (title, personal), (description, personal), (description, r"^" + implicit)]:
        for match in re.finditer("(?:" + cue + r")([^,.!?;|:\n<>]{1,80})", text, re.I):
            place = re.split(r"\b(?:and|but|where|offering|teaching|providing|with)\b", match.group(1), maxsplit=1, flags=re.I)[0].strip()
            if place:
                declarations.append(place)
    row["currentLocalityDeclarations"] = list(dict.fromkeys(declarations))
    broader = {"uk", "the uk", "united kingdom", "the united kingdom", "england", "great britain", "south east england"}
    known = {*BUCKS, "buckinghamshire", "bucks", *CHILDCARE_DISTRICTS}
    for place in declarations:
        value = place.casefold()
        local = value in broader or any(re.fullmatch(re.escape(town) + r"(?:\s+(?:in\s+)?(?:buckinghamshire|bucks|england|uk))?", value) for town in known)
        if not local:
            row["locationConflict"] = True
            row["currentLocationNeedsReview"] = True
    return row


def listing_records(text, url):
    document = Document(text).root
    host = urllib.parse.urlsplit(url).hostname
    rows = []
    if host == 'www.tuitionlist.co.uk':
        seen = set()
        for card in document.walk():
            headings = [n for n in card.walk() if n.tag == 'h2']
            if len(headings) != 1:
                continue
            link = card.first(lambda n: n.tag == 'a' and n.text() == 'View profile')
            source = admitted_url(urllib.parse.urljoin(url, link.attrs.get('href', ''))) if link else None
            header = parent_node(card, headings[0])
            location = next((n for n in header.children if isinstance(n, Node) and n.tag == 'p'), None) if header else None
            info = parent_node(card, header) if header else None
            labels = [n.text() for child in info.children if isinstance(child, Node) and child is not header
                      and child.tag == 'div' for n in child.walk() if n.tag == 'span'] if info else []
            parts = [n.text() if isinstance(n, Node) else n for n in location.children] if location else []
            place = ' '.join(part.strip() for part in parts if part.strip().casefold() not in ('online', 'in-person'))
            if source and source not in seen and place.strip() and labels:
                seen.add(source)
                row = public_person(host, source.rsplit('-', 1)[-1], headings[0].text(),
                    re.sub(r'\s*,\s*', ', ', place.strip()), labels, source)
                rows.append(dict(row, evidenceKind='directory_listing', listingUrl=url))
        return rows
    for card in document.walk():
        if host == "www.uktutors.com" and card.has_class("searchresult"):
            title = card.first(lambda n: n.has_class("top_bars"))
            link = title.first(lambda n: n.tag == "a") if title else None
            source = admitted_url(urllib.parse.urljoin(url, link.attrs.get("href", ""))) if link else None
            info = card.first(lambda n: n.has_class("searchresult_tutor_info"))
            subjects = [n.text() for n in info.walk() if n.tag == "a" and "/subject/" in n.attrs.get("href", "")] if info else []
            if source:
                slug = urllib.parse.urlsplit(source).path.rsplit("/", 1)[-1]
                town = next((t for t in sorted(BUCKS, key=len, reverse=True) if slug.startswith(t.replace(" ", "-") + "-")), "")
                row = public_person(host, urllib.parse.urlsplit(source).path.split("/")[2], link.text(), town, subjects, source)
                rows.append(dict(row, evidenceKind="directory_listing", localityEvidence="profile_url_hint_only", listingUrl=url))
        if host == "tutordex.net" and card.has_class("lg:col-span-2"):
            name = card.first(lambda n: n.tag == "h2")
            role = card.first(lambda n: n.tag == "p" and n.has_class("uppercase"))
            link = card.first(lambda n: n.tag == "a" and n.attrs.get("href", "").startswith("/tutor/"))
            source = admitted_url(urllib.parse.urljoin(url, link.attrs["href"])) if link else None
            claim = re.fullmatch(r"(.+?) (?:Teacher|Tutor) in (.+)", role.text()) if role else None
            if source and name and claim:
                row = public_person(host, urllib.parse.urlsplit(source).path.split("/")[2], name.text(), claim.group(2), [claim.group(1)], source)
                rows.append(dict(row, evidenceKind="directory_listing", listingUrl=url))
        if host == "tutorextra.co.uk" and card.has_class("user-content-table"):
            name = card.first(lambda n: n.has_class("name"))
            link = name.first(lambda n: n.tag == "a") if name else None
            source = admitted_url(urllib.parse.urljoin(url, link.attrs.get("href", ""))) if link else None
            address = card.first(lambda n: n.has_class("address-top"))
            location = class_text(address, "sm") if address else ""
            role = card.first(lambda n: n.has_class("usertype"))
            if source and link and role and re.search(r"\b(?:Tutor|Teacher)\b", role.text()):
                row = public_person(host, urllib.parse.urlsplit(source).path.rsplit("-", 1)[-1], link.text(), location.split(",", 1)[0].strip(), [role.text()], source)
                rows.append(dict(row, evidenceKind="directory_listing", listingUrl=url))
        if host == "www.childcare.co.uk" and card.has_class("search-result"):
            person_id, name = card.attrs.get("data-user-id", ""), card.attrs.get("data-screenname", "")
            if person_id.isdigit() and name:
                location = class_text(card, "distance")
                match = re.fullmatch(r"Private Tutor in (.+)", location)
                rows.append({"name": name, "personId": person_id, "platform": "childcare.co.uk",
                             "sourceUrls": ["https://www.childcare.co.uk/profile/" + person_id], "listingUrl": url,
                             "localityClaim": match.group(1) if match else "", "services": [],
                             "evidenceKind": "directory_listing", "outreachEligible": False})
        if host == "tutorperch.com" and card.attrs.get("data-slot") == "card":
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
    if p.hostname == 'thetutorsassociation.wildapricot.org':
        from tta_scraper import parse_profile
        return parse_profile(document, url)
    elif p.hostname == 'www.tuitionlist.co.uk':
        if not re.fullmatch(r'/tutor/[a-z0-9-]+-[a-f0-9]{8}', p.path):
            return None
        names = [n for n in document.walk() if n.tag == 'h1']
        subjects = named_section(document, 'Subjects and levels')
        location = named_section(document, 'Lesson location')
        statement = location.first(lambda n: n.tag == 'p') if location else None
        labels = [n.text() for n in subjects.walk() if n.tag == 'span' and n.text()] if subjects else []
        claim = re.match(re.escape(names[0].text()) + r'\s+is listed in\s+(.+?)\s*\.', statement.text()) if len(names) == 1 and statement else None
        if not claim or not labels:
            return None
        about = named_section(document, 'About')
        description = about.first(lambda n: n.tag == 'p') if about else None
        row = public_person(p.hostname, p.path.rsplit('-', 1)[-1], names[0].text(),
            re.sub(r'\s*,\s*', ', ', claim.group(1).strip()), labels, url)
        row['contactRouteNote'] = 'Public profile; no email or phone inferred. Enquiry forms are not used for research or marketing.'
        return annotate_advert(row, description=description.text() if description else '')
    elif p.hostname == "www.tutorhunt.com":
        if not re.fullmatch(r"/users/[a-z0-9-]{1,64}/", p.path):
            return None
        header = document.first(lambda n: n.attrs.get("id") == "profilenameinfo")
        names = [n for n in header.walk() if n.attrs.get('id') == 'profilenametxt'] if header else []
        if len(names) > 1:
            return None
        name = names[0] if names else header.first(lambda n: n.tag == "h1") if header else None
        location = header.first(lambda n: n.attrs.get("id") == "profileloc") if header else None
        table = document.first(lambda n: n.tag == "table" and n.has_class("profilesubjectstattable"))
        info = document.first(lambda n: n.has_class("card") and name and n.first(
            lambda c: c.tag == "h2" and c.text().casefold() == "information about " + name.text().casefold()))
        subjects = [n.text() for n in table.walk() if n.tag == "td" and n.has_class("marksub") and n.text()] if table else []
        if not name or not name.text() or not location or not location.text() or not subjects or not info:
            return None
        row = annotate_advert(public_person(p.hostname, p.path.split('/')[2], name.text(), location.text(), subjects, url), description=info.text())
        row["advertisedUnavailable"] = bool(re.search(r"\bNot available to contact\b", document.text(), re.I))
        return row
    elif p.hostname == "www.uktutors.com":
        if not re.fullmatch(r"/tutor/\d+/[a-z0-9-]+", p.path):
            return None
        header = document.first(lambda n: n.tag == "h1" and re.fullmatch(r"(.+?) - (.+) tutor", n.text()))
        claim = re.fullmatch(r"(.+?) - (.+) tutor", header.text()) if header else None
        info = document.first(lambda n: n.tag == "h2" and claim and n.text() == "Information About " + claim.group(1) + ":")
        table = document.first(lambda n: n.attrs.get("id") == "profile_subjects")
        if not claim or not info or not table:
            return None
        subjects = [n.text() for n in table.walk() if n.tag == "strong"]
        # Level labels such as '11+ (secondary)' describe ages, not entrance exams.
        row = public_person(p.hostname, p.path.split("/")[2], claim.group(1), claim.group(2), subjects, url)
        about = document.first(lambda n: any(isinstance(c, Node) and c.tag == "b" and c.text() == "Personal Description:" for c in n.children))
        parts, collecting = [], False
        for child in about.children if about else []:
            if isinstance(child, Node) and child.tag == "b":
                if collecting:
                    break
                collecting = child.text() == "Personal Description:"
            elif collecting:
                parts.append(child.text() if isinstance(child, Node) else child)
        return annotate_advert(row, description=" ".join(" ".join(parts).split()))
    elif p.hostname == "tutordex.net":
        if not re.fullmatch(r"/tutor/\d+/[a-z0-9-]+", p.path):
            return None
        header = document.first(lambda n: any(isinstance(c, Node) and c.tag == "h1" for c in n.children)
                                and any(isinstance(c, Node) and c.tag == "p" and re.fullmatch(r"Private .+ Tutor in .+", c.text()) for c in n.children))
        if not header:
            return None
        name = header.first(lambda n: n.tag == "h1")
        role = header.first(lambda n: n.tag == "p" and n.text().startswith("Private "))
        location = role.first(lambda n: n.tag == "a" and re.fullmatch(r"/uk/academic/town/[a-z0-9-]+", n.attrs.get("href", "")))
        subjects = [n.text() for n in role.walk() if n.tag == "a" and n.attrs.get("href", "").startswith("/uk/academic/subject/")]
        if not location or not name.text():
            return None
        summary = document.first(lambda n: n.tag == "meta" and n.attrs.get("name") == "description")
        return annotate_advert(public_person(p.hostname, p.path.split("/")[2], name.text(), location.text(), subjects, url),
                               description=summary.attrs.get("content", "") if summary else "")
    elif p.hostname == "tutorextra.co.uk":
        if not re.fullmatch(r"/profile/[a-z0-9-]+/\d+-\d+", p.path):
            return None
        header = document.first(lambda n: n.has_class("name-type"))
        name = class_text(header, "fullname") if header else ""
        role = class_text(header, "usertype") if header else ""
        claim = re.fullmatch(r"(.+?) - (?:Tutor|Teacher)(?:, [A-Za-z ]+)? in (.+)", name)
        if not claim or not re.search(r"\b(?:Tutor|Teacher)\b", role) or INSTITUTION.search(role):
            return None
        address = document.first(lambda n: n.has_class("select-address"))
        places = [n.text().split(",", 1)[0].strip() for n in address.walk() if n.tag == "option"] if address else []
        table = document.first(lambda n: n.tag == "table" and n.has_class("responsive-table")
                               and n.first(lambda c: c.tag == "th" and c.text() == "Subject") is not None)
        subjects = []
        if table:
            for tr in table.walk():
                cells = [c for c in tr.children if isinstance(c, Node) and c.tag == "td"] if tr.tag == "tr" else []
                if cells and cells[0].text():
                    subjects.append(cells[0].text())
        row = public_person(p.hostname, p.path.rsplit("-", 1)[-1], claim.group(1), places[0] if places else claim.group(2), subjects, url)
        row["additionalLocalityClaims"] = places[1:]
        if any(place.lower() != row["localityClaim"].lower() for place in places[1:]):
            row["locationConflict"] = True
        title = document.first(lambda n: n.tag == "h1")
        row["institution"] = bool(title and re.search(r"\b(?:education company|tuition centre|learning centre)\b", title.text(), re.I))
        return annotate_advert(row, title.text() if title else "")
    elif p.hostname == "www.childcare.co.uk":
        if not re.fullmatch(r"/profile/\d+", p.path):
            return None
        header = document.first(lambda n: n.has_class("profile-header--info-wrapper"))
        name = header.first(lambda n: n.tag == "h1") if header else None
        role = header.first(lambda n: n.tag == "h3") if header else None
        location = re.fullmatch(r"Private Tutor in (.+?)(?: \(also available[^)]*\))?", role.text()) if role else None
        if not name or not name.text() or not location:
            return None
        services = [n.text() for n in document.walk() if n.has_class("tag") and n.has_class("positive")
                    and (ACADEMIC.search(n.text()) or NON_TUITION.search(n.text()))]
        county_claim = None
        for script in document.walk():
            if script.tag != "script" or script.attrs.get("type") != "application/ld+json":
                continue
            try:
                data = json.loads("".join(c for c in script.children if isinstance(c, str)))
            except (ValueError, TypeError):
                continue
            for item in data if isinstance(data, list) else [data]:
                if not isinstance(item, dict) or item.get("@type") != "BreadcrumbList" or not isinstance(item.get("itemListElement"), list):
                    continue
                for crumb in item["itemListElement"]:
                    if isinstance(crumb, dict) and crumb.get("name") == "Private Tutors in Buckinghamshire" and crumb.get("item") == "https://www.childcare.co.uk/search/Private-Tutors/Buckinghamshire":
                        county_claim = "Buckinghamshire"
        about = document.first(lambda n: any(isinstance(c, Node) and c.tag == "h3" and c.text() == "About Me" for c in n.children))
        description = " ".join(c.text() for c in about.children if isinstance(c, Node) and c.tag == "p") if about else ""
        return annotate_advert({"name": name.text(), "personId": p.path.split("/")[-1], "platform": "childcare.co.uk", "sourceUrls": [url],
                "localityClaim": location.group(1), "directoryCountyCategory": county_claim, "services": services,
                "evidenceKind": "public_person_profile", "outreachEligible": False, "institution": False}, description=description)
    elif p.hostname == "tutorperch.com":
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
        locality = location.text() if location else "Online" if len(crumbs) > 1 and crumbs[1].lower() == "online" else crumbs[2] if len(crumbs) >= 3 and locality_status(crumbs[2]) == "advertised_bucks_locality" else ""
        title = section.first(lambda n: n.tag == "h1")
        description = section.first(lambda n: n.attrs.get("id") == "card_ad_description")
        return annotate_advert({"name": name.text(), "personId": person_id, "platform": "findtutors.co.uk", "sourceUrls": [url],
                "localityClaim": locality, "services": [subject.text() if subject else ""],
                "evidenceKind": "public_advert_person", "outreachEligible": False,
                "institution": not academy or not academy.has_class("non-academy")}, title.text() if title else "", description.text().removeprefix("Advert description ") if description else "")
    return None


def merge_profiles(rows):
    merged = {}
    for row in rows:
        key = (row["platform"], row.get("personId") or row["sourceUrls"][0])
        qualifies = exclusion_reason(row) is None
        target = merged.setdefault(key, dict(row, services=[], sourceUrls=[], pageEvidence=[], hasQualifyingTutorAdvert=False))
        target["hasQualifyingTutorAdvert"] = bool(target["hasQualifyingTutorAdvert"] or qualifies)
        target["nonTuitionAdvert"] = not target["hasQualifyingTutorAdvert"] and bool(target.get("nonTuitionAdvert") or row.get("nonTuitionAdvert"))
        for field in ("services", "sourceUrls"):
            target[field] = list(dict.fromkeys([*target[field], *row.get(field, [])]))
        if target.get("localityClaim") != row.get("localityClaim"):
            target["locationConflict"] = True
        target["locationConflict"] = bool(target.get("locationConflict") or row.get("locationConflict"))
        target["institution"] = bool(target.get("institution") or row.get("institution"))
        evidence = {(item["url"], item.get("sha256")): item for item in [*target["pageEvidence"], *row.get("pageEvidence", [])]}
        target["pageEvidence"] = list(evidence.values())
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
        self.host_spacing = {}
        self.request_times = {}
        path = root / 'request-times.json'
        if path.exists():
            with path.open('rb') as stream:
                raw = stream.read(16385)
            values = json.loads(raw) if len(raw) <= 16384 else None
            if (not isinstance(values, dict) or any(host not in HOSTS or type(stamp) not in (int, float)
                or not math.isfinite(stamp) or stamp < 0 for host, stamp in values.items())):
                raise ValueError('Invalid cached public request times')
            self.request_times = values

    def configure_robots(self, host, parser):
        rate = parser.request_rate(AGENT)
        interval = rate.seconds / rate.requests if rate and rate.requests > 0 else 0
        self.host_spacing[host] = max(self.spacing, parser.crawl_delay(AGENT) or 0, interval)

    def request_delay(self, url):
        host = urllib.parse.urlsplit(url).hostname
        interval = self.host_spacing.get(host, self.spacing)
        recent = interval - (time.monotonic() - self.last_request.get(host, 0))
        saved = interval - max(0, time.time() - self.request_times[host]) if host in self.request_times else 0
        return max(0, recent, saved)

    def can_start_before(self, url, deadline):
        # Cached observations need no HTTP request or host delay.
        return (self.root / 'receipts' / (self.key(url) + '.json')).exists() or time.time() + self.request_delay(url) < deadline

    def fetch_delay(self, url):
        if (self.root / 'receipts' / (self.key(url) + '.json')).exists():
            return 0
        return self.request_delay(url)

    def key(self, url):
        return hashlib.sha256(url.encode()).hexdigest()

    def body_path(self, url):
        return self.root / "pages" / (self.key(url) + ".html")

    def download(self, url):
        host = urllib.parse.urlsplit(url).hostname
        delay = self.request_delay(url)
        if delay > 0:
            time.sleep(delay)
        self.last_request[host] = time.monotonic()
        self.request_times[host] = time.time()
        save(self.root / 'request-times.json', self.request_times)
        request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "text/html,text/plain"})
        with urllib.request.build_opener(RestrictedRedirect(), urllib.request.HTTPSHandler(context=tls_context())).open(request, timeout=20) as response:
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
            return decode_page(body, saved.get("contentType", ""))
        try:
            body, content_type, status = self.download(url)
            if "html" not in content_type and "text/plain" not in content_type:
                raise ValueError("Unsupported content type")
            path = self.body_path(url)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(body)
            path.chmod(0o600)
            save(receipt, {"url": url, "sha256": hashlib.sha256(body).hexdigest(), "status": status, "observedAt": now(), "contentType": content_type})
            return decode_page(body, content_type)
        except (OSError, ValueError) as error:
            detail = 'certificate_verification_failed' if isinstance(getattr(error, 'reason', None), ssl.SSLCertVerificationError) else None
            save(receipt, {"url": url, "error": type(error).__name__, "reason": detail,
                           "httpStatus": getattr(error, 'code', None), "observedAt": now()})
            return None


def linked_pages(text, url):
    links = []
    for node in Document(text).root.walk():
        value = node.attrs.get("href") if node.tag in ("a", "link") else None
        if value and (candidate := admitted_url(urllib.parse.urljoin(url, value))):
            links.append(candidate)
    return list(dict.fromkeys(links))


def listing_profile_urls(cards, host, scope=None):
    """Queue local listing profiles without treating their role as a subject."""
    urls = []
    for row in cards:
        if scope and query_priority(services_text(row), scope) == 2:
            continue
        reason = exclusion_reason(row)
        # TutorExtra's dyslexia and home-education cards can advertise only a
        # tutor/teacher role. Fetch the person's profile to inspect its actual
        # subjects; the listing itself remains ineligible for contact export.
        deferred_subject = (host == 'tutorextra.co.uk'
            and row.get('platform') == 'tutorextra.co.uk'
            and row.get('evidenceKind') == 'directory_listing'
            and reason == 'needs_subject_review'
            and any(re.search(r'\b(?:Tutor|Teacher)\b', service, re.I)
                    for service in row.get('services', [])))
        if (host == 'www.childcare.co.uk'
            or (locality_status(row['localityClaim']) == 'advertised_bucks_locality'
                and (reason is None or deferred_subject))):
            urls.append(row['sourceUrls'][0])
    return urls


def local_directory_link(value, source):
    parsed, origin = urllib.parse.urlsplit(value), urllib.parse.urlsplit(source)
    if parsed.hostname != origin.hostname:
        return False
    if parsed.hostname == 'thetutorsassociation.wildapricot.org':
        from tta_scraper import PROFILE_PATH
        return bool(PROFILE_PATH.fullmatch(parsed.path))
    if parsed.hostname == "www.tutorhunt.com":
        if parsed.path.startswith('/users/'):
            return True
        match = re.fullmatch(r"/(?:subjects/[a-z0-9-]+/)?locations/([a-z0-9-]+)/", parsed.path)
        return bool(match and locality_status(match.group(1).replace('-', ' ')) == 'advertised_bucks_locality')
    if parsed.hostname == "www.uktutors.com":
        if parsed.path == "/county/buckinghamshire-tutors/":
            return True
        match = re.fullmatch(r"/town/([a-z0-9-]+)-tutors/", parsed.path)
        return bool(match and locality_status(match.group(1).replace("-", " ")) == "advertised_bucks_locality")
    if parsed.hostname == 'www.tuitionlist.co.uk':
        return parsed.path == '/find-a-tutor'
    if parsed.hostname == "tutordex.net":
        match = re.fullmatch(r"/uk/academic/town/([a-z0-9-]+)(?:/[a-z0-9-]+){0,2}", parsed.path)
        return bool(match and locality_status(match.group(1).replace("-", " ")) == "advertised_bucks_locality")
    if parsed.hostname == "tutorextra.co.uk":
        match = re.fullmatch(r"/search/[a-z0-9-]+-lessons-in/([a-z0-9-]+)", parsed.path)
        return bool(match and locality_status(match.group(1).replace("-", " ")) == "advertised_bucks_locality")
    if parsed.hostname == "www.childcare.co.uk":
        return bool(CHILDCARE_DIRECTORY_PATH.fullmatch(parsed.path))
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
        if not isinstance(urls, list) or len(urls) > 5000 or any(not isinstance(url, str) for url in urls):
            rejected.append(path.name)
            raise ValueError("Seed batch must contain bounded source URLs")
        admitted = list(dict.fromkeys(url for candidate in urls if (url := admitted_url(candidate))))
        priorities = value.get('priorityUrls', [])
        if (not isinstance(priorities, list) or len(priorities) > 100
                or any(not isinstance(url, str) for url in priorities)):
            rejected.append(path.name)
            raise ValueError('Expected at most a hundred priority source URLs')
        priority_urls = [admitted_url(url) for url in priorities]
        if (any(url not in admitted for url in priority_urls)
                or len(set(priority_urls)) != len(priority_urls)):
            rejected.append(path.name)
            raise ValueError('Priority URLs must be unique admitted members of their seed batch')
        for url in admitted:
            if url not in state["seen"] and url not in state["queue"] and len(state["queue"]) < 20000:
                state["queue"].append(url)
        priority_set = set(priority_urls)
        state['queue'] = [url for url in priority_urls if url in state['queue']] + [
            url for url in state['queue'] if url not in priority_set]
        imported.append(path.stem)


def next_page(state, cache=None):
    queue = state["queue"]
    by_host = {}
    for index, url in enumerate(queue):
        by_host.setdefault(urllib.parse.urlsplit(url).hostname, []).append(index)
    def person_page(index):
        path = urllib.parse.urlsplit(queue[index]).path
        return path.startswith(("/profile/", "/t/", "/tutor/", "/users/", "/Sys/PublicProfile/")) or bool(re.search(r"-\d+/?$", path))
    def town_root(index):
        parsed = urllib.parse.urlsplit(queue[index])
        return parsed.hostname == "www.findtutors.co.uk" and not parsed.query and bool(re.fullmatch(r"/tutors-[a-z0-9-]+/", parsed.path))
    candidates = {host: next((index for index in indices if person_page(index)),
        next((index for index in indices if town_root(index)), indices[0]))
        for host, indices in by_host.items()}
    hosts = sorted(by_host)
    if cache:
        delays = {host: cache.fetch_delay(queue[index]) for host, index in candidates.items()}
        ready = [host for host in hosts if delays[host] <= 0]
        earliest = min(delays.values())
        hosts = ready or [host for host in hosts if delays[host] == earliest]
    previous = state.get('lastHost')
    first = urllib.parse.urlsplit(queue[0]).hostname
    if previous:
        host = next((value for value in hosts if value > previous), hosts[0])
    else:
        host = first if first in hosts else hosts[0]
    state["lastHost"] = host
    return queue.pop(candidates[host])


def source_still_producing(job):
    if not job.get("seedSourceRun"):
        return False
    path = Path(job["seedSourceRun"]) / "runtime.json"
    return not path.exists() or json.loads(path.read_text()).get("phase") != "complete"


def write_profile_exports(root, state, phase='running'):
    profiles = merge_profiles(state['records'])
    qualified = [r for r in profiles if profile_locality_status(r) == 'advertised_bucks_locality'
                 and not r.get('locationConflict') and not exclusion_reason(r)]
    save(root / 'profiles.json', profiles)
    save(root / 'bucks-private-tutors.json', qualified)
    export_csv(root, qualified)
    state['updatedAt'] = now()
    save(root / 'state.json', state)
    save(root / 'status.json', {'observedAt': now(), 'pid': os.getpid(), 'phase': phase,
        'attempts': state['attempts'], 'queue': len(state['queue']), 'namedProfiles': len(profiles),
        'bucksPrivateProfiles': len(qualified), 'errors': len(state['errors']), 'paidCostUsd': 0,
        'effectiveSourceSha': state.get('effectiveSourceSha'),
        'cacheReplays': {key: {k: v for k, v in value.items() if k != 'processedKeys'}
                        for key, value in state.get('cacheReplays', {}).items()}})
    return qualified


def replay_cached_profiles(root, state, deadline, clock=time.time, platform='tutorhunt'):
    """Recover pages missed by an older layout parser, without fetching or resetting counters."""
    sources = {'tutorhunt': ('tutorhunt-profile-name-v2', 'https://www.tutorhunt.com/users/'),
               'tta': ('tta-public-fields-v1', 'https://thetutorsassociation.wildapricot.org/Sys/PublicProfile/')}
    if platform not in sources:
        raise ValueError('Unsupported cached profile replay source')
    version, prefix = sources[platform]
    progress = state.setdefault('cacheReplays', {}).setdefault(version,
        {'processedKeys': [], 'processedPages': 0, 'recoveredRecords': 0, 'heldPages': 0})
    done = set(progress['processedKeys'])
    known = {url for row in state['records'] for url in row.get('sourceUrls', [])}
    cache = PageCache(root)
    changed = 0
    for url in state['seen']:
        if clock() >= deadline:
            break
        if not isinstance(url, str) or not url.startswith(prefix) or admitted_url(url) != url:
            continue
        key = cache.key(url)
        if key in done:
            continue
        try:
            if url not in known:
                receipt_path = root / 'receipts' / (key + '.json')
                receipt = json.loads(receipt_path.read_text())
                page = cache.body_path(url)
                if page.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('Cached page exceeded eight MB')
                body = page.read_bytes()
                if (receipt.get('error') or receipt.get('status') != 200 or receipt.get('url') != url
                    or hashlib.sha256(body).hexdigest() != receipt.get('sha256')):
                    raise ValueError('Cached profile receipt or body mismatch')
                row = profile_record(decode_page(body, receipt.get('contentType', '')), url)
                if not row:
                    raise ValueError('Cached page did not establish its own profile identity')
                row['pageEvidence'] = [{'url': url, 'observedAt': receipt['observedAt'],
                    'sha256': receipt['sha256'], 'receipt': str(receipt_path)}]
                state['records'].append(row)
                known.add(url)
                progress['recoveredRecords'] += 1
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            progress['heldPages'] += 1
        done.add(key)
        progress['processedKeys'].append(key)
        progress['processedPages'] += 1
        changed += 1
        if changed % 50 == 0:
            write_profile_exports(root, state, 'replaying_cached_profiles')
    progress['updatedAt'] = now()
    write_profile_exports(root, state, 'replaying_cached_profiles')
    return {k: v for k, v in progress.items() if k != 'processedKeys'}


def run(root, seeds, hours, page_limit, target):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "owner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job_path, state_path = root / "job.json", root / "state.json"
        if job_path.exists():
            job = json.loads(job_path.read_text())
            from configuration import profile_page_ceiling
            if not 1 <= job['pageLimit'] <= profile_page_ceiling(job):
                raise ValueError('Prepared public-crawl page limit exceeded')
        else:
            admitted = list(dict.fromkeys(admitted_url(s) for s in seeds if admitted_url(s)))
            if not admitted:
                raise ValueError("No permitted source seeds")
            started = datetime.datetime.now(datetime.timezone.utc)
            job = {"startedAt": started.isoformat(), "deadlineUnix": started.timestamp() + hours * 3600, "pageLimit": page_limit,
                   "target": target, "seeds": admitted, "paidBudget": 0,
                   "scope": "Public individual tutor profiles across subjects in Bucks and Milton Keynes; exclude institutions. Locality is advertised tuition locality, not verified residence."}
            save(job_path, job)
        state = json.loads(state_path.read_text()) if state_path.exists() else {"queue": job["seeds"], "seen": [], "records": [], "attempts": 0, "errors": [], "phase": "running"}
        state['effectiveSourceSha'] = loaded_source_sha()
        scope = load_scope(Path(job['seedSourceRun'])) if job.get('seedSourceRun') else None
        state['discoveryScopeSha256'] = hashlib.sha256((Path(job['seedSourceRun']) / 'discovery-scope.json').read_bytes()).hexdigest() if scope else None
        replay_cached_profiles(root, state, job['deadlineUnix'])
        replay_cached_profiles(root, state, job['deadlineUnix'], platform='tta')
        cache, robots = PageCache(root), {}
        while state["attempts"] < job["pageLimit"] and time.time() < job["deadlineUnix"]:
            try:
                import_seed_batches(root, state)
            except (OSError, ValueError, TypeError) as error:
                state["errors"].append({"reason": "seed_batch_rejected", "type": type(error).__name__})
            if not state["queue"]:
                if source_still_producing(job):
                    state["updatedAt"] = now()
                    save(state_path, state)
                    save(root / 'status.json', {'observedAt': now(), 'pid': os.getpid(), 'phase': 'awaiting_sources',
                        'attempts': state['attempts'], 'queue': 0, 'errors': len(state['errors']),
                        'bucksPrivateProfiles': len(json.loads((root / 'bucks-private-tutors.json').read_text())),
                        'paidCostUsd': 0, 'effectiveSourceSha': state.get('effectiveSourceSha'),
                        'cacheReplays': {key: {k: v for k, v in value.items() if k != 'processedKeys'}
                                        for key, value in state.get('cacheReplays', {}).items()}})
                    time.sleep(5)
                    continue
                break
            url = next_page(state, cache)
            if url in state["seen"]:
                continue
            state["seen"].append(url)
            host = urllib.parse.urlsplit(url).hostname
            if host not in robots:
                robot_url = "https://" + host + "/robots.txt"
                if not cache.can_start_before(robot_url, job['deadlineUnix']):
                    state['seen'].remove(url)
                    state['queue'].insert(0, url)
                    break
                robot_text = cache.fetch(robot_url)
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(robot_text.splitlines() if robot_text else ["User-agent: *", "Disallow: /"])
                robots[host] = parser
                cache.configure_robots(host, parser)
            if not robots[host].can_fetch(AGENT, url):
                state["errors"].append({"url": url, "reason": "robots_disallowed"})
                save(state_path, state)
                continue
            if not cache.can_start_before(url, job['deadlineUnix']):
                state['seen'].remove(url)
                state['queue'].insert(0, url)
                break
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
                        local_urls = listing_profile_urls(cards, host, scope)
                        links = linked_pages(text, url)
                        links = [link for link in links if local_directory_link(link, url)]
                        if scope:
                            links = [link for link in links if '/profile/' in link or '/users/' in link
                                or '/tutor/' in link or query_priority(urllib.parse.urlsplit(link).path.replace('/', ' ').replace('-', ' '), scope) < 2]
                        if host == "www.findtutors.co.uk":
                            links = [link for link in links if "/tutor/" not in link and not re.search(r"-\d+/?$", urllib.parse.urlsplit(link).path)]
                        state["queue"].extend(link for link in [*local_urls, *links] if link not in state["seen"] and link not in state["queue"])
                        state["queue"] = state["queue"][:20000]
                else:
                    state["errors"].append({"url": url, "reason": "fetch_failed"})
            except (OSError, ValueError, KeyError, TypeError) as error:
                state["errors"].append({"url": url, "reason": type(error).__name__})
            qualified = write_profile_exports(root, state)
            if len(qualified) >= job["target"] and not job.get('continueUntilDeadline'):
                break
        state["phase"] = "stage_complete"
        save(state_path, state)
        rows = json.loads((root / "bucks-private-tutors.json").read_text()) if (root / "bucks-private-tutors.json").exists() else []
        export_csv(root, rows)
        save(root / "status.json", {"observedAt": now(), "pid": os.getpid(), "phase": state["phase"], "attempts": state["attempts"], "queue": len(state["queue"]),
                                    "bucksPrivateProfiles": len(rows), "errors": len(state["errors"]), "paidCostUsd": 0,
                                    "effectiveSourceSha": state.get('effectiveSourceSha')})


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--seed", action="append", default=[])
    parser.add_argument("--hours", type=float, default=3)
    parser.add_argument("--pages", type=int, default=5000)
    parser.add_argument("--target", type=int, default=1000)
    args = parser.parse_args(argv)
    ceiling = 20000
    job_path = args.run_dir / 'job.json'
    if job_path.exists():
        from configuration import profile_page_ceiling
        try:
            ceiling = profile_page_ceiling(json.loads(job_path.read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            parser.error('Prepared public-crawl authorization is invalid')
    if not 0 < args.hours <= 12 or not 1 <= args.pages <= ceiling or not 1 <= args.target <= 5000:
        parser.error('Choose up to twelve hours and the validated public-crawl page allowance')
    run(args.run_dir, args.seed, args.hours, args.pages, args.target)


if __name__ == '__main__':
    main()
