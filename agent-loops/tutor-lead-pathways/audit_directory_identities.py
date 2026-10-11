#!/usr/bin/env python3
"""Audit cached public profiles without fetching pages or approving outreach."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import urllib.parse

from directory_profiles import Document, admitted_url, decode_page, exclusion_reason, merge_profiles, now, profile_locality_status, profile_record, save

AMBIGUOUS_UK_PLACES = {"burnham", "haddenham", "penn", "sands", "bourton", "denham"}
ELEVEN_PLUS = re.compile(r"\b11\s*\+|\b(?:11|eleven)[ -]+plus\b", re.I)


def ambiguous_county(value):
    # Exact OS Open Names matches include places outside Bucks, or no Bucks match.
    # A county search heading cannot disambiguate the person's own bare place name.
    return value.split(",", 1)[0].strip().casefold() in AMBIGUOUS_UK_PLACES and not re.search(r"\b(?:buckinghamshire|bucks)\b", value, re.I)


def normalize_name(value):
    value = re.sub(r"^(?:(?:mr|mrs|ms|miss|dr|prof)\.?\s+)+", "", value.strip(), flags=re.I)
    return " ".join(re.sub(r"[^\w ]", " ", value.casefold()).split())


def professional_name(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "www.findtutors.co.uk" or not re.fullmatch(r"/tutor/[a-z0-9-]+\.htm", parsed.path):
        return None
    name = normalize_name(re.sub(r"[-\d]+$", "", parsed.path.rsplit("/", 1)[-1][:-4]).replace("-", " "))
    return name if len(name.split()) > 1 else None


def audit_rows(root, rows):
    result = []
    seen = set()
    for row in rows:
        key = row["platform"] + ":" + row["personId"]
        if key in seen or row.get("locationConflict") or exclusion_reason(row) or profile_locality_status(row) != "advertised_bucks_locality" or row.get("outreachEligible"):
            raise ValueError("Invalid or repeated qualified identity: " + key)
        seen.add(key)
        evidence = row.get("pageEvidence", [])
        if not evidence or set(row["sourceUrls"]) != {e["url"] for e in evidence}:
            raise ValueError("Missing source evidence: " + key)
        hints = set()
        replayed = []
        eleven_plus = any(ELEVEN_PLUS.search(service) for service in row["services"])
        for item in evidence:
            url = item["url"]
            if admitted_url(url) != url:
                raise ValueError("Source URL left admitted public paths")
            cache_key = hashlib.sha256(url.encode()).hexdigest()
            receipt = json.loads((root / "receipts" / (cache_key + ".json")).read_text())
            body = (root / "pages" / (cache_key + ".html")).read_bytes()
            if receipt.get("url") != url or receipt.get("sha256") != item["sha256"] or hashlib.sha256(body).hexdigest() != item["sha256"]:
                raise ValueError("Page evidence hash mismatch: " + key)
            text = decode_page(body, receipt.get("contentType", ""))
            parsed = profile_record(text, url)
            if not parsed or parsed["personId"] != row["personId"] or parsed["platform"] != row["platform"]:
                raise ValueError("Public person identity does not match its page: " + key)
            replayed.append(parsed)
            if row["platform"] == "findtutors.co.uk":
                doc = Document(text).root
                section = doc.first(lambda n: n.attrs.get("id") == "sectionaddetail")
                info = section.first(lambda n: n.has_class("info")) if section else None
                link = info.first(lambda n: n.attrs.get("id") == "lnkProfile") if info else None
                title = section.first(lambda n: n.tag == "h1") if section else None
                eleven_plus = bool(eleven_plus or (title and ELEVEN_PLUS.search(title.text()) and not re.search(r"\b(?:passed|sat|took)\b", title.text(), re.I)))
                hint = professional_name(urllib.parse.urljoin(url, link.attrs.get("href", ""))) if link else None
                if hint:
                    hints.add(hint)
        if len(hints) > 1:
            raise ValueError("Conflicting own professional names: " + key)
        own = merge_profiles(replayed)[0]
        if own.get("locationConflict") or exclusion_reason(own) or profile_locality_status(own) != "advertised_bucks_locality" or own["name"] != row["name"] or set(own["services"]) != set(row["services"]):
            raise ValueError("Qualified profile does not match replayed page evidence: " + key)
        result.append(dict(row, key=key, professionalNameHint=next(iter(hints), None), elevenPlusServiceClaim=eleven_plus))
    return result


def reconcile(rows, decisions):
    by_key = {r["key"]: r for r in rows}
    if len(by_key) != len(rows):
        raise ValueError("Duplicate input identity")
    parent = {key: key for key in by_key}

    def root(key):
        while key != parent[key]:
            key = parent[key]
        return key

    for decision in decisions.get("merges", []):
        members = decision["members"]
        if len(set(members)) < 2 or not decision.get("reason") or any(key not in by_key for key in members):
            raise ValueError("Invalid reviewed merge")
        actual = {(e["url"], e["sha256"]) for key in members for e in by_key[key]["pageEvidence"]}
        expected = {(e["url"], e["sha256"]) for e in decision.get("evidence", [])}
        if not expected or not expected <= actual or any(not any((e["url"], e["sha256"]) in expected for e in by_key[key]["pageEvidence"]) for key in members):
            raise ValueError("Reviewed merge evidence does not match each member")
        representative = min(root(key) for key in members)
        for key in members:
            parent[root(key)] = representative

    components = defaultdict(list)
    for key in sorted(by_key):
        components[root(key)].append(by_key[key])
    identities = []
    for key, members in sorted(components.items()):
        identity = dict(members[0], canonicalId="tutor:" + hashlib.sha256(key.encode()).hexdigest(), memberKeys=[m["key"] for m in members])
        identity["pageEvidence"] = list({(e["url"], e["sha256"]): e for m in members for e in m["pageEvidence"]}.values())
        identity["sourceUrls"] = sorted({u for m in members for u in m.get("sourceUrls", [])})
        identity["services"] = sorted({v for m in members for v in m.get("services", [])})
        identity["elevenPlusServiceClaim"] = any(m.get("elevenPlusServiceClaim", False) for m in members)
        identity["aliases"] = sorted({normalize_name(m["name"]) for m in members} | {m["professionalNameHint"] for m in members if m.get("professionalNameHint")})
        identity["aliases"] = sorted(set(identity["aliases"]) | {alias.split()[0] for alias in identity["aliases"] if alias})
        identities.append(identity)

    # This intentionally holds uncertain records rather than merging first names.
    # Two different complete own professional names do not collide on a first name.
    suspected = {r["key"]: set() for r in identities}
    names = defaultdict(list)
    for row in identities:
        for alias in row["aliases"]:
            names[alias].append(row)
    for alias, group in names.items():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                left_full, right_full = left.get("professionalNameHint"), right.get("professionalNameHint")
                if len(alias.split()) == 1 and left_full and right_full and left_full != right_full:
                    continue
                suspected[left["key"]].add(right["key"])
                suspected[right["key"]].add(left["key"])
    accepted, held, groups, visited = [], [], [], set()
    by_identity = {r["key"]: r for r in identities}
    for key in sorted(by_identity):
        if key in visited:
            continue
        pending, component = [key], set()
        while pending:
            member = pending.pop()
            if member not in component:
                component.add(member)
                pending.extend(suspected[member] - component)
        visited.update(component)
        members = [by_identity[k] for k in sorted(component)]
        selected, withheld = [], []
        # A possible-match chain is not proof of one person. Prefer complete own
        # professional names, retaining a pairwise non-colliding subset. An
        # incomplete-name bridge stays held without collapsing distinct names.
        for member in sorted(members, key=lambda r: (not bool(r.get("professionalNameHint")), r["key"])):
            if any(other["key"] in suspected[member["key"]] for other in selected):
                withheld.append(dict(member, reason="possible_duplicate_requires_review"))
            else:
                selected.append(member)
        accepted.extend(selected)
        held.extend(withheld)
        if len(members) > 1:
            groups.append({"reason": "possible_duplicate_requires_review", "memberKeys": [m["key"] for m in members],
                           "acceptedKeys": [m["key"] for m in selected], "heldKeys": [m["key"] for m in withheld]})
    accepted.sort(key=lambda r: r["key"])
    held.sort(key=lambda r: r["key"])
    return accepted, held, groups


def run(root, output, decisions_path):
    path = root / "bucks-private-tutors.json"
    raw = path.read_bytes()
    rows = audit_rows(root, json.loads(raw))
    decisions = json.loads(decisions_path.read_text()) if decisions_path else {"merges": []}
    scope_held = [dict(r, reason="ambiguous_county_place_requires_review") for r in rows if ambiguous_county(r["localityClaim"])]
    accepted, held, groups = reconcile([r for r in rows if not ambiguous_county(r["localityClaim"])], decisions)
    save(output / "tutors.json", accepted)
    save(output / "held-possible-duplicates.json", held)
    save(output / "held-ambiguous-locations.json", scope_held)
    save(output / "possible-duplicate-groups.json", groups)
    save(output / "audit.json", {"at": now(), "inputSha256": hashlib.sha256(raw).hexdigest(),
        "decisionsSha256": hashlib.sha256(decisions_path.read_bytes()).hexdigest() if decisions_path else None,
        "sourceProfiles": len(rows), "reviewedMergeReduction": len(rows) - len(accepted) - len(held) - len(scope_held),
        "acceptedIdentities": len(accepted), "heldPossibleDuplicates": len(held), "possibleDuplicateGroups": len(groups),
        "heldAmbiguousLocations": len(scope_held),
        "withExplicitElevenPlusServiceClaim": sum(r["elevenPlusServiceClaim"] for r in accepted),
        "pageEvidenceVerified": sum(len(r["pageEvidence"]) for r in rows), "paidCalls": 0, "outreachApproved": 0,
        "qualification": "Public advertised local private-tutor profiles. Uncertain matching names held; locality, availability and consent are not independently verified."})
    with (output / "tutors.csv").open("w", newline="") as stream:
        (output / "tutors.csv").chmod(0o600)
        fields = ["canonicalId", "name", "localityClaim", "services", "sourceUrls", "memberKeys", "elevenPlusServiceClaim", "outreachEligible"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(r, **{f: "; ".join(r.get(f, [])) for f in ["services", "sourceUrls", "memberKeys"]}) for r in accepted)
    return json.loads((output / "audit.json").read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--decisions", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.run_dir, args.output_dir, args.decisions)))
