#!/usr/bin/env python3
"""Plan a selected delivery anywhere; --execute verifies and hardlinks on Studio.

The Studio ledger remains the sole budget/review authority. Delivery ledger JSON
is a read-only historical snapshot. No rendering, providers or approval writes.
"""
import argparse
import contextlib
import datetime
import fcntl
import html
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import uuid

SPEC = importlib.util.spec_from_file_location("delivery_preparation", Path(__file__).with_name("prepare-review.py"))
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)
ledger = prepare.ledger
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = Path.home() / "render-tmp/smart-cub-refresh-20261016/delivery/video-delivery"
KINDS = {"marketing": "marketing", "cta": "cta", "teaching_social_derivative": "teaching"}
LAUNCHER = '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=current/index.html"><title>Smart Cub Learning videos</title><a href="current/index.html">Open the approved video delivery</a>\n'


def selection(value, kinds=None, ids=None, aspect="both"):
    if aspect not in (*ledger.ASPECTS, "both"):
        raise ValueError("Unknown delivery aspect")
    known = {identity: item for identity, item in value["items"].items() if item.get("in_current_inventory")}
    if ids and set(ids) - set(known):
        raise ValueError("Unknown or retired delivery IDs")
    selected = [(identity, item, view) for identity, item in known.items()
                if (not ids or identity in ids) and (not kinds or KINDS[item["kind"]] in kinds)
                for view in (ledger.ASPECTS if aspect == "both" else (aspect,))]
    if not selected:
        raise ValueError("No current output variants selected")
    return selected


def plan(value, selected):
    states = {}
    for _, item, aspect in selected:
        state = item["aspects"][aspect]["state"]
        states[state] = states.get(state, 0) + 1
    return {"state": "plan_only_no_current_byte_verification", "selected_output_count": len(selected),
            "selected_item_count": len({identity for identity, _, _ in selected}),
            "selected_kinds": sorted({KINDS[item["kind"]] for _, item, _ in selected}),
            "selected_aspects": sorted({aspect for _, _, aspect in selected}), "last_inventory_states": states,
            "total_requested_outputs": sum(len(item["aspects"]) for item in value["items"].values() if item.get("in_current_inventory")),
            "note": "Plan uses the last ledger snapshot only. Execute verifies every selected approval, byte and receipt on Studio."}


def selected_state(value, keys):
    """Freeze review authority relevant to this delivery, without the live budget."""
    items = {}
    for identity, aspect in keys:
        item = value["items"][identity]
        selected = items.setdefault(identity, {"metadata": {key: part for key, part in item.items() if key != "aspects"},
                                               "aspects": {}})
        selected["aspects"][aspect] = item["aspects"][aspect]
    source = value.get("source_snapshot") or {}
    projection = {"policy": value["policy"], "asset_replacement": value["asset_replacement"],
                  "global_source": {"fingerprint": source.get("fingerprint"), "complete": source.get("complete")},
                  "selected": items}
    bound = ledger.production.binding(value)
    if bound:
        projection["production_roots"] = (bound if any(item["metadata"]["kind"] == "marketing" for item in items.values())
                                          else {"canonical_root": bound["canonical_root"]})
    registrations = value.get("teaching_source_overrides") or {}
    selected_overrides = {identity.removeprefix("teaching:"): registrations[identity.removeprefix("teaching:")]
                          for identity in items if identity.startswith("teaching:")
                          and identity.removeprefix("teaching:") in registrations}
    if selected_overrides:
        # Registration can change before a fresh inventory updates item metadata.
        # Bind it at the final authority lock without coupling unrelated repairs.
        projection["teaching_source_overrides"] = selected_overrides
    return projection


def require_selected_state(value, keys, expected):
    try:
        current = ledger.digest_json(selected_state(value, keys))
    except KeyError as exc:
        raise ValueError("Selected approval authority changed; retry after explicit review") from exc
    if current != expected:
        raise ValueError("Selected approval authority changed before activation; retry after explicit review")


@contextlib.contextmanager
def activation_guard(authority, keys, expected):
    # This is the same lock as reserve/settle/record. Hold it ONLY for the final
    # JSON projection check and pointer swap, never any source/media hashing.
    with authority.with_suffix(authority.suffix + ".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        require_selected_state(ledger.read(authority), keys, expected)
        yield


def distribution_metadata(root, identity, review, retained):
    restrictions = review.get("explicit_judgement", {}).get("distribution_restrictions")
    if restrictions is None or restrictions == []:
        return {}
    values = restrictions if isinstance(restrictions, list) else [restrictions]
    provenance = {}
    for restriction in values:
        if not isinstance(restriction, dict) or any(not isinstance(restriction.get(key), str) or not restriction[key].strip()
                                                  for key in ("scope", "recheckBy", "action")):
            raise ValueError("Distribution restrictions need explicit scope, recheckBy and action")
        try:
            datetime.date.fromisoformat(restriction["recheckBy"])
        except ValueError as exc:
            raise ValueError("Distribution recheckBy must retain an explicit ISO date") from exc
        # This is a distribution note, never an inferred expiry or automatic ban.
        references = restriction.get("evidence")
        if references is not None:
            snapshots = prepare.evidence_for(root, references)
            if any(retained.get(entry["path"]) != entry["sha256"] for entry in snapshots):
                raise ValueError("Distribution restriction evidence is outside the approved evidence chain")
            provenance.update({entry["path"]: entry for entry in snapshots})
            continue
        # Existing audits already contain datedAdvice objects. A decision may
        # retain one verbatim without duplicating its enclosing audit reference.
        matched = False
        for filename, checksum in retained.items():
            if not filename.endswith(".json"):
                continue
            value = ledger.read(root / filename)
            rows = value.get("videos", []) if isinstance(value, dict) else []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                advice = row.get("datedAdvice", {})
                if not isinstance(advice, dict):
                    continue
                if row.get("id") == identity and all(advice.get(key) == restriction[key] for key in ("scope", "recheckBy", "action")):
                    provenance[filename] = prepare.exact(root, filename, checksum)
                    matched = True
        if not matched:
            raise ValueError("Distribution restriction needs retained hashed audit evidence matching this output")
    return {"distribution_restrictions": restrictions, "distribution_restriction_evidence": list(provenance.values())}


def approved_entry(preparation, identity, item, aspect):
    variant = item["aspects"][aspect]
    review = variant.get("review") or {}
    required = preparation.value["policy"]["required_checks"]
    checks = review.get("checks", {})
    if variant.get("state") != "passed" or not variant.get("ready_for_review") or review.get("verdict") != "pass" \
            or review.get("input_sha256") != variant.get("input_sha256") \
            or review.get("output_sha256") != variant.get("output", {}).get("sha256") \
            or set(checks) != set(required) or any(checks[name] != "pass" for name in required if name != "selected_cta_lip_sync") \
            or checks.get("selected_cta_lip_sync") not in {"pass", "not_applicable"} \
            or any(finding.get("status") not in {"fixed", "refuted"} for finding in review.get("findings", [])) \
            or not review.get("evidence"):
        raise ValueError(f"{identity}/{aspect}: delivery requires a current explicit pass with no open findings")
    evidence = {entry["path"]: entry["sha256"] for entry in review["evidence"]}
    for filename, checksum in evidence.items():
        prepare.exact(preparation.root, filename, checksum)
    jobs = []
    for filename in evidence:
        if Path(filename).name.startswith("request-") and filename.endswith(".json"):
            observation = ledger.read(preparation.root / filename)
            # A selected ending can retain a standalone CTA observation as
            # supporting evidence. Its bytes stay checked above; only a matching
            # output's primary advisory can satisfy this output's review gate.
            if observation.get("id") != identity or observation.get("aspect") != aspect \
                    or observation.get("version", {}).get("output_sha256") != variant["output"]["sha256"]:
                continue
            candidate = preparation.candidate(identity, aspect, filename)
            if any(evidence.get(entry["path"]) != entry["sha256"] for entry in candidate["evidence"]):
                raise ValueError(f"{identity}/{aspect}: approval does not retain the current complete evidence chain")
            if candidate["lip_sync_selected"] and checks["selected_cta_lip_sync"] != "pass":
                raise ValueError("Selected lip sync cannot be waived for delivery")
            explicit = review.get("explicit_judgement", {})
            dispositions = explicit.get("dispositions", {})
            recorded_findings = {finding.get("id"): finding for finding in review.get("findings", [])}
            for issue in candidate["issues"]:
                judgement = dispositions.get(issue["id"], recorded_findings.get(issue["id"], {}))
                allowed = {"fixed", "refuted"} | ({"optional_not_required"} if issue["kind"] == "optional_advice" else set())
                if judgement.get("status") not in allowed or not judgement.get("rationale", "").strip():
                    raise ValueError("Approved evidence still contains an unreconciled model or technical finding")
            reconciliations = explicit.get("coverage_reconciliations", {})
            if any(not reconciliations.get(warning["id"], {}).get("rationale", "").strip() for warning in candidate["warnings"]):
                raise ValueError("Approved evidence still contains an unreconciled model coverage warning")
            jobs.append(candidate)
    if len(jobs) != 1:
        raise ValueError(f"{identity}/{aspect}: require exactly one approved full-clip advisory and current receipt proof")
    base = item.get("skill_key", identity)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", base):
        raise ValueError("Unsafe portable delivery filename")
    context = preparation.review_items[identity]["context"]
    return {"id": identity, "kind": KINDS[item["kind"]], "aspect": aspect,
            "title": re.sub(r"\*+", "", item.get("title", identity)),
            "cta_id": context["approved_cta"]["id"], "cta_destination": context["approved_cta"].get("url"),
            "audience": context.get("audience"),
            "path": f"{KINDS[item['kind']]}/{aspect}/{base}.mp4",
            "source_project_path": variant["output"]["path"], "sha256": variant["output"]["sha256"],
            "bytes": variant["output"]["bytes"], "reviewed_at": review["recorded_at"],
            "reviewer": review["reviewer"], "review_summary": review["summary"], "checks": checks,
            **({"production_family": "marketing" if item["kind"] == "marketing" else "canonical",
                "source_root": str(preparation.root), "source_fingerprint": preparation.global_source["fingerprint"],
                "render_source_fingerprint": preparation.render_source["fingerprint"]}
               if ledger.production.binding(preparation.value) else {}),
            "evidence": review["evidence"], **distribution_metadata(preparation.root, identity, review, evidence)}


def index_html(scope, entries, generation):
    data = json.dumps(entries, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    title = f"{scope['selected_output_count']} approved files, {scope['selected_item_count']} videos"
    scope_text = "Selected scope: " + ", ".join(scope["selected_kinds"]) + "; " + ", ".join(scope["selected_aspects"]) + "."
    return '''<!doctype html><html lang="en-GB"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Cub Learning video delivery</title><style>
:root{font-family:Inter,system-ui,sans-serif;color:#075565;background:#FFF4DF}body{max-width:1200px;margin:auto;padding:32px}h1{margin-bottom:8px;font-size:clamp(1.7rem,4vw,2.5rem)}.gold{height:5px;background:#F3B52A;width:90px;margin:18px 0}.note{line-height:1.5}nav{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}input,select,button{font:inherit;padding:10px;border:1px solid #075565;border-radius:8px;background:#fff;color:#075565}input{flex:1;min-width:180px}main{display:grid;grid-template-columns:minmax(250px,1fr) minmax(300px,1.5fr);gap:24px}.list{max-height:65vh;overflow:auto}button{display:block;text-align:left;width:100%;margin-bottom:8px;cursor:pointer}button:hover,button[aria-pressed=true]{background:#F3B52A}small{display:block;margin-top:5px}video{width:100%;max-height:60vh;background:#075565;border-radius:12px}a{color:#075565}article{min-width:0}code{overflow-wrap:anywhere;font-size:.8rem}.links{display:flex;gap:18px;margin:18px 0}footer{margin-top:30px;border-top:1px solid #075565;padding-top:18px;font-size:.85rem}@media(max-width:760px){body{padding:18px}main{display:flex;flex-direction:column-reverse}.list{max-height:45vh}}
</style><header><p>SMART CUB LEARNING</p><h1>Approved video delivery</h1><div class="gold"></div><p>''' + html.escape(title) + '''</p><p class="note">''' + html.escape(scope_text) + ''' Every listed file passed its exact-version review. This selected delivery does not certify completion of videos outside this scope.</p></header>
<nav aria-label="Filter videos"><input id="search" type="search" aria-label="Search videos" placeholder="Search title or video ID"><select id="kind" aria-label="Kind"><option value="">All selected kinds</option></select><select id="aspect" aria-label="Aspect"><option value="">Both aspects</option><option>tall</option><option>wide</option></select></nav>
<p id="count" aria-live="polite"></p><main><section class="list" id="list" aria-label="Videos"></section><article><h2 id="title">Choose a video</h2><p id="restriction" class="note" hidden></p><video id="player" controls preload="metadata"></video><p id="details"></p><p><code id="hash"></code></p><a id="download" download hidden>Download MP4</a></article></main>
<footer><div class="links"><a id="manifest">Checksummed manifest</a><a id="checksums">SHA-256 checksums</a><a id="ledger">Read-only review snapshot</a></div>This snapshot is historical evidence. The Studio ledger remains the sole review and budget authority. Keep this folder together to preserve portable links.</footer>
<script id="entries" type="application/json">''' + data + '''</script><script>
const entries=JSON.parse(document.getElementById('entries').textContent), generation=''' + json.dumps(generation) + ''';
// Pin links to this immutable generation even when opened through current/.
const base=location.pathname.endsWith('/generations/'+generation+'/index.html')?new URL('.',location.href):new URL('../generations/'+generation+'/',location.href);
for(const [id,file]of [['manifest','manifest.json'],['checksums','SHA256SUMS'],['ledger','ledger-snapshot.json']])document.getElementById(id).href=new URL(file,base);
const kind=document.getElementById('kind'),aspect=document.getElementById('aspect'),search=document.getElementById('search'),list=document.getElementById('list');
for(const value of [...new Set(entries.map(e=>e.kind))]){const option=document.createElement('option');option.value=value;option.textContent=value;kind.append(option)}
const restrictions=entry=>Array.isArray(entry.distribution_restrictions)?entry.distribution_restrictions:entry.distribution_restrictions?[entry.distribution_restrictions]:[];
const show=(entry,button)=>{document.querySelectorAll('[aria-pressed]').forEach(b=>b.setAttribute('aria-pressed','false'));button.setAttribute('aria-pressed','true');document.getElementById('title').textContent=entry.title;const notes=restrictions(entry),note=document.getElementById('restriction');note.textContent=notes.map(r=>'Distribution scope: '+r.scope+' Recheck by '+r.recheckBy+'. '+r.action).join(' ');note.hidden=notes.length===0;document.getElementById('details').textContent=entry.id+' · '+entry.kind+' · '+entry.aspect;document.getElementById('hash').textContent='SHA-256 '+entry.sha256;document.getElementById('player').src=new URL(entry.path,base);const link=document.getElementById('download');link.href=new URL(entry.path,base);link.download=entry.path.split('/').pop();link.hidden=false};
const filter=()=>{const query=search.value.toLowerCase();const selected=entries.filter(e=>(!kind.value||e.kind===kind.value)&&(!aspect.value||e.aspect===aspect.value)&&(e.id+' '+e.title).toLowerCase().includes(query));list.replaceChildren();for(const entry of selected){const button=document.createElement('button');button.type='button';button.textContent=entry.title;button.setAttribute('aria-pressed','false');const small=document.createElement('small');small.textContent=entry.id+' · '+entry.aspect+(restrictions(entry).length?' · Dated advice: recheck '+restrictions(entry).map(r=>r.recheckBy).join(', '):'');button.append(small);button.onclick=()=>show(entry,button);list.append(button)}document.getElementById('count').textContent=selected.length+' matching files'};
for(const input of [kind,aspect,search])input.addEventListener('input',filter);filter();
</script></html>\n'''


def bundle_path(root, relative):
    if Path(relative).is_absolute() or not relative or ".." in Path(relative).parts:
        raise ValueError("Unsafe bundle path")
    path = root / relative
    if any((root / Path(*Path(relative).parts[:index])).is_symlink() for index in range(1, len(Path(relative).parts) + 1)) \
            or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Symlink or escaped file inside delivery")
    return path


def checked_bundle(directory):
    manifest_path = bundle_path(directory, "manifest.json")
    checksum_path = bundle_path(directory, "SHA256SUMS")
    sums = dict(line.split("  ", 1)[::-1] for line in checksum_path.read_text().splitlines())
    if sums.get("manifest.json") != ledger.sha256(manifest_path):
        raise ValueError("Existing delivery manifest checksum mismatch")
    manifest = ledger.read(manifest_path)
    expected = manifest["files"]
    if set(sums) != set(expected) | {"manifest.json"}:
        raise ValueError("Existing delivery checksum inventory mismatch")
    for filename, checksum in expected.items():
        path = bundle_path(directory, filename)
        if not path.is_file() or ledger.sha256(path) != checksum or sums.get(filename) != checksum:
            raise ValueError("Existing delivery file mismatch: " + filename)
    return manifest


def current_bundle(destination):
    current = destination / "current"
    if not current.is_symlink():
        if current.exists():
            raise ValueError("Existing delivery current entry is not a managed symlink")
        return None
    target = os.readlink(current)
    if not re.fullmatch(r"generations/[a-f0-9]{24}", target):
        raise ValueError("Existing delivery pointer is not a managed generation")
    directory = destination / target
    if directory.is_symlink():
        raise ValueError("Existing delivery generation is a symlink")
    return checked_bundle(directory)


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def delivery_lock(destination):
    with (destination / ".delivery.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def execute(root, authority, destination, kinds=None, ids=None, aspect="both"):
    ledger.require_studio()
    root, authority, destination = root.resolve(), authority.resolve(), destination.expanduser().absolute()
    if destination.is_symlink() or destination.resolve().is_relative_to(root) or root.is_relative_to(destination.resolve()):
        raise ValueError("Delivery must be a separate directory outside the source project")
    snapshot = authority.read_bytes()
    authority_hash = hashlib.sha256(snapshot).hexdigest()
    snapshot_value = json.loads(snapshot)
    bound = ledger.production.binding(snapshot_value)
    if bound:
        for project in (Path(bound["canonical_root"]), Path(bound["root"])):
            if destination.resolve().is_relative_to(project) or project.is_relative_to(destination.resolve()):
                raise ValueError("Delivery must be outside every configured production root")
    snapshot_captured_at = ledger.now()
    original_selection = selection(snapshot_value, kinds, ids, aspect)
    keys = [(identity, view) for identity, _, view in original_selection]
    selected_hash = ledger.digest_json(selected_state(snapshot_value, keys))
    preparation = prepare.Preparation(root, authority)
    require_selected_state(preparation.value, keys, selected_hash)
    require_selected_state(ledger.read(authority), keys, selected_hash)
    selected = [(identity, preparation.value["items"][identity], view) for identity, view in keys]
    entries = [approved_entry(preparation.for_kind(item["kind"]), identity, item, view) for identity, item, view in selected]
    scope = plan(preparation.value, selected)
    scope["state"] = "selected_outputs_exact_version_verified"
    scope.pop("last_inventory_states")
    scope.pop("note")
    implementation_hash = ledger.sha256(Path(__file__))
    generation = ledger.digest_json({"ledger_sha256": authority_hash, "scope": scope, "entries": entries,
                                     "delivery_implementation_sha256": implementation_hash})[:24]
    destination.mkdir(parents=True, exist_ok=True)
    staging = None
    with delivery_lock(destination):
        old = current_bundle(destination)
        if old:
            previous = {entry["path"]: entry["sha256"] for entry in old["videos"]}
            if any(entry["path"] in previous and previous[entry["path"]] != entry["sha256"] for entry in entries):
                raise ValueError("Selected output differs from an existing delivered file; preserve and reconcile that delivery")
        launcher = destination / "index.html"
        if launcher.exists() and (launcher.is_symlink() or launcher.read_text() != LAUNCHER):
            raise ValueError("Existing root index differs from the managed launcher")
        generations = destination / "generations"
        if generations.is_symlink():
            raise ValueError("Delivery generations directory cannot be a symlink")
        generations.mkdir(exist_ok=True)
        final = generations / generation
        created, activated = False, False
        try:
            if final.is_symlink():
                raise ValueError("Existing generation cannot be a symlink")
            if final.exists():
                existing = checked_bundle(final)
                if existing["videos"] != entries or existing["ledger_sha256"] != authority_hash or existing["scope"] != scope:
                    raise ValueError("Existing generation differs from the exact selected delivery")
            else:
                staging = destination / (".staging-" + uuid.uuid4().hex)
                staging.mkdir()
                files = {}
                for entry in entries:
                    target = staging / entry["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(Path(entry.get("source_root", root)) / entry["source_project_path"], target)  # EXDEV refuses; never copy 70 GB silently.
                    if ledger.sha256(target) != entry["sha256"]:
                        raise ValueError("Output changed during hardlink export")
                    files[entry["path"]] = entry["sha256"]
                ledger_path = staging / "ledger-snapshot.json"
                ledger_path.write_bytes(snapshot)
                ledger_path.chmod(0o444)  # This is a copy, never chmod a hardlinked source MP4.
                files["ledger-snapshot.json"] = ledger.sha256(ledger_path)
                index = staging / "index.html"
                index.write_text(index_html(scope, entries, generation))
                files["index.html"] = ledger.sha256(index)
                manifest = {"schema_version": 1, "generation": generation, "scope": scope,
                            "all_requested_outputs_included": len(selected) == scope["total_requested_outputs"],
                            "source_fingerprint": preparation.global_source["fingerprint"] if not bound else None,
                            **({"production_families": {entry["production_family"]: {
                                "root": entry["source_root"], "source_fingerprint": entry["source_fingerprint"],
                                "render_source_fingerprint": entry["render_source_fingerprint"]} for entry in entries}}
                               if bound else {}),
                            "ledger_sha256": authority_hash, "ledger_snapshot_read_only": True,
                            "ledger_snapshot_captured_at": snapshot_captured_at,
                            "ledger_snapshot_updated_at": snapshot_value.get("updated_at"),
                            "selected_approval_fingerprint": selected_hash,
                            "ledger_snapshot_is_budget_authority": False, "media_storage": "hardlinks",
                            "delivery_implementation_sha256": implementation_hash,
                            "review_evidence_storage": "Retained in the source project; evidence paths are source-project provenance",
                            "videos": entries, "files": files}
                prepare.atomic_json(staging / "manifest.json", manifest)
                sums = {**files, "manifest.json": ledger.sha256(staging / "manifest.json")}
                (staging / "SHA256SUMS").write_text("".join(f"{checksum}  {name}\n" for name, checksum in sorted(sums.items())))
                checked_bundle(staging)
                for filename in ("ledger-snapshot.json", "index.html", "manifest.json", "SHA256SUMS"):
                    with (staging / filename).open("rb") as retained:
                        os.fsync(retained.fileno())
                for directory, _, _ in os.walk(staging, topdown=False):
                    sync_directory(directory)
                os.replace(staging, final)
                sync_directory(generations)
                staging = None
                created = True
            # Reopen fresh authority/context/manifest views and re-hash every
            # selected evidence chain. Unselected reviews and budget writes may
            # continue while these potentially long operations run.
            fresh = prepare.Preparation(root, authority)
            require_selected_state(fresh.value, keys, selected_hash)
            fresh_entries = [approved_entry(fresh.for_kind(fresh.value["items"][identity]["kind"]), identity, fresh.value["items"][identity], view) for identity, view in keys]
            if fresh_entries != entries:
                raise ValueError("Selected exact output or review proof changed before activation")
            checked_bundle(final)
            for kind in {fresh.value["items"][identity]["kind"] for identity, _ in keys}:
                fresh.for_kind(kind).stable()
            if not launcher.exists():
                temporary = destination / (".index-" + uuid.uuid4().hex)
                temporary.write_text(LAUNCHER)
                with temporary.open("rb") as retained:
                    os.fsync(retained.fileno())
                os.replace(temporary, launcher)
            pointer = destination / (".current-" + uuid.uuid4().hex)
            try:
                os.symlink("generations/" + generation, pointer)
                with activation_guard(authority, keys, selected_hash):
                    os.replace(pointer, destination / "current")  # Manifest and index activate together.
                    activated = True
                    sync_directory(destination)
            finally:
                pointer.unlink(missing_ok=True)
        finally:
            if staging and staging.exists():
                shutil.rmtree(staging)
            if created and not activated:
                shutil.rmtree(final)
    return {"state": "selected_approved_delivery_activated", "generation": generation, "scope": scope,
            "index": str(destination / "index.html"), "ledger_authority": "Live Studio ledger remains sole authority; exported JSON is a dated historical snapshot"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--kinds", nargs="+", choices=tuple(KINDS.values()))
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--aspect", choices=("both", "tall", "wide"), default="both")
    args = parser.parse_args()
    try:
        if args.execute:
            result = execute(args.root, args.ledger.expanduser(), args.destination, args.kinds, args.ids, args.aspect)
        else:
            value = ledger.read(args.ledger.expanduser())
            result = plan(value, selection(value, args.kinds, args.ids, args.aspect))
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"deliver-videos: {exc}\n")


if __name__ == "__main__":
    main()
