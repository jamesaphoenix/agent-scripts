#!/usr/bin/env python3
"""Run and observe five finite research paths, then clean their saved evidence."""
import argparse
import collections
import csv
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shlex
import sqlite3
import subprocess
import time

from configuration import launchd_settings, campaign_window

from discovery import (UTC, PaidCache, atomic_json, collect_records, file_hash, hold_reason,
                       merge_businesses, now_iso, parse_time, provider_request,
                       public_url, request_key, validate_plan, website_key, write_exports)

HERE = Path(__file__).resolve().parent
INTERVALS = {"maps": 120, "business": 240, "organic": 240, "directories": 240}
OFFSETS = {"maps": 0, "business": 25, "organic": 50, "directories": 75}


def request_intervals(job):
    value = job.get("requestIntervals", INTERVALS)
    if not isinstance(value, dict) or set(value) != set(INTERVALS) or any(type(v) is not int or not 15 <= v <= 900 for v in value.values()):
        raise ValueError("Every pathway needs a bounded integer request interval")
    return value


def initialize_job(root):
    manifest = json.loads((root / "manifest.json").read_text())
    if (root / "job.json").exists():
        job = json.loads((root / "job.json").read_text())
        for key, value in manifest.items():
            if job.get(key) != value:
                raise ValueError("Original job manifest changed: " + key)
    else:
        started, deadline, cutoff = campaign_window(manifest)
        job = dict(manifest, startedAt=started.isoformat(), deadline=deadline.isoformat(), discoveryDeadline=cutoff.isoformat())
        atomic_json(root / "job.json", job)
    if manifest.get('authorizationSha256') and file_hash(root / 'authorization.json') != manifest['authorizationSha256']:
        raise ValueError('Original campaign authorization changed')
    if manifest.get('crawlerSha256') and file_hash(Path(manifest['crawlerBinary'])) != manifest['crawlerSha256']:
        raise ValueError('Reviewed crawler binary changed')
    if parse_time(job["deadline"]) - parse_time(job["startedAt"]) != datetime.timedelta(hours=job.get('hours', 6)):
        raise ValueError("Original research window changed")
    if parse_time(job["deadline"]) - parse_time(job["discoveryDeadline"]) != datetime.timedelta(minutes=15):
        raise ValueError("Original paid research cutoff changed")
    if job.get('hours') == 12:
        campaign_window(manifest)
        if job['startedAt'] != manifest['authorizedStartedAt'] or job['deadline'] != manifest['authorizedDeadline']:
            raise ValueError('Restart changed the request-anchored window')
    validate_plan(root, job)
    crawl_limits(job)
    launchd_settings(job)
    request_intervals(job)
    return job


def child_environment():
    allowed = ["PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME"]
    return {key: os.environ[key] for key in allowed if key in os.environ}


def sdk_command(root, job, slot):
    # Global instructions/skills point into Desktop. Native launchd file opens can
    # stall behind macOS privacy prompts. Existing loopback SSH avoids that context.
    public_key = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().split()[:2]
    if len(public_key) != 2 or public_key[0] != "ssh-ed25519":
        raise ValueError("Cannot verify the installed loopback SSH host key")
    known_hosts = root / "sdk/ssh-known-hosts"
    known_hosts.parent.mkdir(exist_ok=True, mode=0o700)
    known_hosts.write_text("localhost " + " ".join(public_key) + "\n")
    known_hosts.chmod(0o600)
    environment = [f"{key}={value}" for key, value in child_environment().items()]
    command = shlex.join(["/usr/bin/env", "-i", *environment, job["nodeBinary"], str(HERE / "agentic.mjs"), str(root), str(slot), "ssh_loopback"])
    return ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(known_hosts), "localhost", command]


def load_runtime(root, job):
    path = root / "runtime.json"
    if path.exists():
        state = json.loads(path.read_text())
        if state["runId"] != job["runId"]:
            raise ValueError("Runtime identity changed")
        return state
    start = parse_time(job["startedAt"]).timestamp()
    return {"runId": job["runId"], "phase": "running", "completedQueries": [],
            "nextDue": {lane: start + OFFSETS[lane] for lane in INTERVALS}, "pathFailures": {},
            "nextSdkAt": start, "nextSdkSlot": 0, "crawlNumber": 0, "crawlRoots": [],
            "nextCrawlAt": start + 90, "errors": [], "pid": os.getpid()}


def finished_query(item, cache):
    # Later category pages are only bought when the preceding page proves more results exist.
    payload = item["payload"]
    if item["lane"] != "business" or payload.get("offset", 0) == 0:
        return False
    previous_payload = dict(payload, offset=payload["offset"] - 100)
    previous = cache.get(request_key("business", previous_payload))
    if not previous or previous["status"] != "succeeded":
        return True
    envelope = cache.receipt(previous)
    results = envelope["tasks"][0].get("result") or []
    return not results or results[0].get("total_count", 0) <= payload["offset"]


def next_business_supplement(cache, state):
    # Dense regions actually exceed the two pages in the immutable base plan.
    # Expand only from a successful, hashed receipt: at most five hundred
    # businesses per region and twenty supplementary pages for this window.
    claimed = {item["key"] for item in state.get("businessSupplementaryQueries", []) if "key" in item}
    if len(state.get("businessSupplementaryQueries", [])) >= 20:
        return None
    rows = cache.db.execute("SELECT * FROM requests WHERE lane='business' AND imported=0 AND status='succeeded'").fetchall()
    for row in sorted(rows, key=lambda row: json.loads(row["payload"]).get("offset", 0)):
        payload = json.loads(row["payload"])
        offset = payload.get("offset", 0)
        if payload.get("limit") != 100 or not 100 <= offset < 400:
            continue
        results = cache.receipt(row)["tasks"][0].get("result") or []
        if not results or not results[0].get("items") or results[0].get("total_count", 0) <= offset + 100:
            continue
        next_payload = dict(payload, offset=offset + 100)
        key = request_key("business", next_payload)
        if key not in claimed and not cache.get(key):
            return {"lane": "business", "key": key, "payload": next_payload, "parentKey": row["key"]}
    return None


def next_query(lane, plan, cache, state):
    if lane == "business":
        supplementary = next_business_supplement(cache, state)
        if supplementary:
            return supplementary
    completed = set(state["completedQueries"])
    def group(item):
        if item.get('tactic') == 'public_profiles':
            match = re.search(r'\bsite:([^ ]+)', item['payload'].get('keyword', ''))
            return match.group(1) if match else item['tactic']
        return item.get('tactic', lane)
    # Explore sources and tactics before buying another town from one source.
    # This changes selection order only; every payload and paid key stays fixed.
    counts = collections.Counter(group(item) for item in plan if item['lane'] == lane and item['key'] in completed)
    choices = [item for item in plan if item['lane'] == lane]
    if any('tactic' in item for item in choices):
        feedback = state.get('tacticFeedback', {}) if state.get('adaptiveTactics') else {}
        def measured_priority(item):
            name = group(item)
            observed = feedback.get(name, {})
            attempts = observed.get('requests', 0)
            # Explore every group before following observed yield. A query URL
            # is a provisional reward until the identity audit can attribute it.
            if attempts < 3:
                return (0, counts[name], 0)
            contacts = observed.get('auditedNewIdentitiesAttributed', 0)
            reward = contacts * 10 + observed.get('newResultUrls', 0) * .02
            cost = max(1, observed.get('chargedCents', 0))
            return (1, -reward / cost, counts[name])
        choices.sort(key=measured_priority if feedback else lambda item: counts[group(item)])
    for item in choices:
        if item["lane"] != lane or item["key"] in completed:
            continue
        saved = cache.get(item["key"])
        if saved:
            state["completedQueries"].append(item["key"])
            state.setdefault("reusedQueries", []).append({"key": item["key"], "status": saved["status"]})
            continue
        if 'site:firsttutors.com' in item['payload'].get('keyword', ''):
            state['completedQueries'].append(item['key'])
            state.setdefault('skippedQueries', []).append({'key': item['key'],
                'reason': 'Own source site served its closure notice on 12 October 2026'})
            continue
        if finished_query(item, cache):
            state["completedQueries"].append(item["key"])
            state.setdefault("skippedQueries", []).append({"key": item["key"], "reason": "Previous page did not establish further results"})
            continue
        return item
    return None


def sdk_query_supplements(root, job, cache):
    """Model suggestions become bounded data, with approved geography attached."""
    queries = []
    seen = set()
    for path in sorted((root / 'sdk/results').glob('slot-*.json')):
        for keyword in json.loads(path.read_text()).get('nextQueries', [])[:12]:
            if (not isinstance(keyword, str) or not 3 <= len(keyword) <= 700 or
                    re.search(r'[\x00-\x1f]|https?://', keyword) or
                    not re.search(r'\btutor\w*\b|\btuition\b|\b11[ +%-]|\beleven plus\b', keyword, re.I)):
                continue
            town = next((town for town in job['towns'] if re.search(r'\b' + re.escape(town['name']) + r'\b', keyword, re.I)), None)
            if not town:
                continue
            payload = {'keyword': keyword.replace('11+', '11%2B'), 'location_coordinate': town['coordinate'] + ',200',
                       'language_code': 'en', 'se_domain': 'google.co.uk', 'depth': 30}
            lane = 'directories' if 'site:' in keyword else 'organic'
            key = request_key(lane, payload)
            if key not in seen:
                seen.add(key)
                queries.append({'lane': lane, 'town': town['name'], 'tactic': 'sdk_expansion',
                                'key': key, 'payload': payload, 'sdkResultSha256': file_hash(path)})
    return queries


def provider_is_failure(response, row):
    if not row or row["status"] not in ("failed", "uncertain"):
        return False
    # A billed search with no matches is a terminal result, not a provider outage.
    tasks = response.get("tasks", []) if response else []
    return not (response and response.get("status_code") == 20000 and len(tasks) == 1 and tasks[0].get("status_code") == 40102)


def directory_fallback(item, response):
    if item["lane"] not in ("directories", "organic") or not response or response.get("status_code") != 20000:
        return None
    tasks = response.get("tasks") or []
    if len(tasks) != 1 or tasks[0].get("status_code") != 40101:
        return None
    keyword = item["payload"].get("keyword", "")
    # A simpler query may avoid engine failures on grouped source, subject or
    # document operators. It is a different paid identity, never a replay of an
    # uncertain request. The caller admits it against the deadline and budget.
    def first_alternative(match):
        terms = re.split(r'\s+OR\s+', match.group(1))
        operators = all(re.match(r'(?:site:|inurl:|filetype:|")', term) for term in terms)
        return terms[0] if len(terms) > 1 and operators else match.group(0)
    simplified = re.sub(r'\(([^()]+)\)', first_alternative, keyword)
    return dict(item["payload"], keyword=simplified) if simplified != keyword else None


def request_before_deadline(cache, lane, payload, deadline):
    # A primary request can finish after the paid cutoff. Its different-query
    # fallback must check admission again rather than inherit an earlier check.
    if time.time() >= deadline:
        return None
    return provider_request(cache, lane, payload)


def runner_snapshot(root, job, cache, state):
    statuses = collections.Counter()
    lanes = collections.defaultdict(collections.Counter)
    for row in cache.db.execute("SELECT lane,status,imported FROM requests").fetchall():
        if not row["imported"]:
            statuses[row["status"]] += 1
            lanes[row["lane"]][row["status"]] += 1
    sdk = json.loads((root / "sdk/status.json").read_text()) if (root / "sdk/status.json").exists() else {}
    summary = json.loads((root / "exports/summary.json").read_text()) if (root / "exports/summary.json").exists() else {}
    value = {"runId": job["runId"], "observedAt": now_iso(), "startedAt": job["startedAt"],
             "deadline": job["deadline"], "phase": state["phase"], "pid": state["pid"],
             "budget": cache.usage(), "queryStates": dict(statuses), "pathways": dict(lanes),
             "completedPlanEntries": len(state["completedQueries"]), "sdk": sdk,
             "exports": summary, "crawlBatches": state["crawlNumber"], "recentErrors": state["errors"][-8:]}
    value["lastCrawlerSuccessAt"] = state.get("lastCrawlerSuccessAt")
    value['preparedSourceSha'] = job.get('sourceSha')
    deployment = root / 'deployment/latest.json'
    value['effectiveSourceSha'] = json.loads(deployment.read_text())['sourceSha'] if deployment.exists() else job.get('sourceSha')
    value["consecutivePathFailures"] = dict(state["pathFailures"])
    atomic_json(root / "status.json", value)
    return value


def status(root):
    job = json.loads((root / "job.json").read_text())
    validate_plan(root, job)
    value = json.loads((root / "status.json").read_text())
    cache = PaidCache(root, job["budgetCents"], job["priorExposureCents"])
    value["budget"] = cache.usage()
    cache.db.close()
    if (root / "sdk/status.json").exists():
        value["sdk"] = json.loads((root / "sdk/status.json").read_text())
    runtime_path = root / "runtime.json"
    if runtime_path.exists():
        completed = set(json.loads(runtime_path.read_text())["completedQueries"])
        remaining = collections.Counter(item["lane"] for item in json.loads((root / "plan.json").read_text())
                                        if item["key"] not in completed)
        value["remainingPlanQueries"] = {lane: remaining[lane] for lane in INTERVALS}
    lock_path = root / "run.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            value["processLive"] = False
            fcntl.flock(lock, fcntl.LOCK_UN)
        except BlockingIOError:
            pid = value["pid"]
            result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10)
            value["processLive"] = result.returncode == 0 and str(HERE / "run.py") in result.stdout and str(root) in result.stdout
    return value


def seed_previous_urls(job):
    # This newly authorised job has a larger per-site allowance. Historical
    # evidence stays available, but does not suppress revisiting those sites.
    # start_crawl independently preserves this job's fetched-site lineage.
    if job.get("historicalEvidenceRoots") and crawl_limits(job)["pagesPerSite"] > 4:
        return set()
    visited = set()
    history = Path(job["historyRoot"])
    for path in history.glob("tracelearn-tutors-*/discovery.json"):
        state = json.loads(path.read_text())
        for result in state["crawl"]["results"]:
            if result.get("evidence"):
                visited.add(result["node"]["url"])
    return visited


def prioritise_seed_records(records, job):
    local, outside = set(), set()
    for folder in job.get("historicalEvidenceRoots", []):
        for filename, target in [("bucks-listings-research.csv", local), ("outside-recorded-area.csv", outside)]:
            path = Path(folder) / "exports" / filename
            if path.exists():
                with path.open(newline="") as stream:
                    target.update(key for row in csv.DictReader(stream) if (key := website_key(row.get("website"))))
    def priority(record):
        key = website_key(record.get("website"))
        return 0 if key in local else 2 if key in outside else 1
    # A recorded address prioritises research; it never grants qualification.
    return sorted(records, key=priority)


def crawl_limits(job):
    # Manifests without this field retain the completed run's original limits.
    limits = {"depth": 1, "branchSites": 15, "sites": 50, "batchPages": 200,
              "pagesPerSite": 4, "workers": 4, "rootsPerBatch": 10}
    supplied = job.get("crawlLimits", {})
    if not isinstance(supplied, dict) or set(supplied) - set(limits):
        raise ValueError("Unknown crawl limits")
    limits.update(supplied)
    bounds = {"depth": (0, 2), "branchSites": (0, 15), "sites": (1, 500), "batchPages": (1, 1000),
              "pagesPerSite": (1, 8), "workers": (1, 4), "rootsPerBatch": (1, 50)}
    for name, (minimum, maximum) in bounds.items():
        if type(limits[name]) is not int or not minimum <= limits[name] <= maximum:
            raise ValueError("Crawl limit outside reviewed bounds: " + name)
    return limits


def launch_crawl(job, folder, arguments):
    with (folder / "stdout.log").open("a") as stdout, (folder / "stderr.log").open("a") as stderr:
        process = subprocess.Popen([job["crawlerBinary"], "run", "--state", str(folder / "crawl.json"), *arguments],
                                   stdout=stdout, stderr=stderr, env=child_environment(), start_new_session=True)
    return process, folder


def start_crawl(root, job, cache, state, previous_urls):
    limits = crawl_limits(job)
    checkpoints = [(path, json.loads(path.read_text())) for path in sorted((root / "crawls").glob("*/crawl.json"))]
    remaining = job["freePageBudget"] - sum(saved.get("pages", 0) for _, saved in checkpoints)
    if remaining <= 0:
        return None
    # Resume queued work before marking additional roots as owned. Saved topology
    # and lineage remain unchanged; only its attempted-page allowance increases.
    for path, saved in checkpoints:
        if not saved.get("frontier") and not saved.get("inFlight"):
            continue
        pages = saved.get("pages", 0)
        ceiling = min(1000, pages + min(limits["batchPages"], remaining))
        if ceiling > pages:
            return launch_crawl(job, path.parent, ["--resume", "--pages", str(ceiling)])
    # A site fetched as an external descendant must not become a new root in a
    # later batch. Otherwise its depth and branch counters would reset forever.
    visited_sites = {website_key(url) for url in previous_urls}
    for _, saved in checkpoints:
        for result in saved.get("results", []):
            url = (result.get("evidence") or {}).get("url") or result.get("node", {}).get("url")
            if url:
                visited_sites.add(website_key(url))
    exported = root / 'exports/businesses.json'
    # The owner already publishes this snapshot on its export cadence. Reading
    # it avoids another full historic receipt scan before every crawler batch.
    records = json.loads(exported.read_text()) if exported.exists() else merge_businesses(collect_records(cache, root))
    records = prioritise_seed_records(records, job)
    done = set(state["crawlRoots"]) | set(state.get("crawlRejected", []))
    seeds = []
    source_count = 0
    for record in records:
        url = public_url(record.get("website"))
        if not url or url in done or website_key(url) in visited_sites:
            continue
        if record.get("holdReason") and record["holdReason"] != "source_directory_or_job_page":
            continue
        if record.get("holdReason") == "source_directory_or_job_page":
            if source_count >= 1 or re.search(r"(?i)job|vacancy|teachingpersonnel|studysmarter|womenforhire", url):
                continue
            source_count += 1
        elif record.get("websiteKind") != "professional_profile" and not re.search(
                r"(?i)tutor|tuition|11\s*\+|11[\s-]*plus|eleven[\s-]*plus|education|dyslexia",
                " ".join([record.get("name") or "", record.get("category") or "", url])):
            continue
        seeds.append(url)
        if len(seeds) >= 500:
            break
    if not seeds:
        return None
    # The reviewed Go binary owns URL/site admission. Reuse its policy without
    # a network request instead of duplicating its social/directory deny list.
    crawl_dir = root / "crawls"
    crawl_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    seed_file = crawl_dir / "seed-candidates.txt"
    seed_file.write_text("\n".join(seeds) + "\n")
    result = subprocess.run([job["crawlerBinary"], "filter-seeds", "--seeds", str(seed_file)],
                            capture_output=True, text=True, check=True, timeout=30, env=child_environment())
    filtered = json.loads(result.stdout)
    atomic_json(crawl_dir / f"seed-filter-{state['crawlNumber']:03d}.json", filtered)
    state.setdefault("crawlRejected", []).extend(row["url"] for row in filtered["rejected"])
    seeds = filtered["permitted"][:limits["rootsPerBatch"]]
    if not seeds:
        return None
    folder = root / "crawls" / f"batch-{state['crawlNumber']:03d}"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / "seeds.txt").write_text("\n".join(seeds) + "\n")
    result = launch_crawl(job, folder, ["--seeds", str(folder / "seeds.txt"), "--depth", str(limits["depth"]),
                                      "--branch-sites", str(limits["branchSites"]), "--sites", str(limits["sites"]),
                                      "--pages", str(min(limits["batchPages"], remaining)),
                                      "--pages-per-site", str(limits["pagesPerSite"]), "--workers", str(limits["workers"])])
    state["crawlNumber"] += 1
    state["crawlRoots"].extend(seeds)
    return result


def finish_crawl(job, folder):
    checkpoint = folder / "crawl.json"
    if not checkpoint.exists():
        raise ValueError("Crawler did not save a checkpoint")
    with (folder / "evidence.jsonl").open("w") as stream:
        subprocess.run([job["crawlerBinary"], "export", "--state", str(checkpoint), "--kind", "evidence"],
                       stdout=stream, stderr=subprocess.PIPE, env=child_environment(), check=True, timeout=30)


def save_error(state, lane, error):
    state["errors"].append({"at": now_iso(), "lane": lane, "error": type(error).__name__ + ": " + str(error)[:200]})
    state["errors"] = state["errors"][-100:]


def run(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Existing run owner retains the original job")
            return
        job = initialize_job(root)
        cache = PaidCache(root, job["budgetCents"], job["priorExposureCents"])
        cache.recover_interrupted()
        state = load_runtime(root, job)
        if state["phase"] == "complete":
            print("Original research run is already complete")
            return
        state["pid"] = os.getpid()
        state["phase"] = "running"
        state['adaptiveTactics'] = job.get('adaptiveTactics', False)
        plan = json.loads((root / "plan.json").read_text())
        intervals = request_intervals(job)
        original_deadline = parse_time(job["deadline"]).timestamp()
        paid_deadline = parse_time(job["discoveryDeadline"]).timestamp()
        previous_urls = seed_previous_urls(job)
        sdk_child = crawl_child = None
        interrupted = False
        def stop_handler(signum, frame):
            nonlocal interrupted
            interrupted = True
        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, stop_handler)
        write_exports(root, cache)
        runner_snapshot(root, job, cache, state)
        while not interrupted and time.time() < paid_deadline:
            current = time.time()
            if sdk_child and sdk_child.poll() is not None:
                if sdk_child.returncode:
                    save_error(state, "codex_oauth", ValueError("SDK process exited " + str(sdk_child.returncode)))
                sdk_child = None
            if crawl_child and crawl_child[0].poll() is not None:
                try:
                    finish_crawl(job, crawl_child[1])
                    state["lastCrawlerSuccessAt"] = now_iso()
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    save_error(state, "crawler", error)
                crawl_child = None
            if not sdk_child and current >= state["nextSdkAt"] and state["nextSdkSlot"] < job["maximumSdkTurns"]:
                sdk_status = json.loads((root / "sdk/status.json").read_text()) if (root / "sdk/status.json").exists() else {}
                if sdk_status.get("totalInputTokens", 0) + sdk_status.get("totalOutputTokens", 0) < job["sdkTokenCeiling"]:
                    slot = state["nextSdkSlot"]
                    state["nextSdkSlot"] += 1
                    state["nextSdkAt"] += 1800
                    atomic_json(root / "runtime.json", state)
                    (root / "sdk").mkdir(exist_ok=True)
                    with (root / "sdk/stdout.log").open("a") as stdout, (root / "sdk/stderr.log").open("a") as stderr:
                        sdk_child = subprocess.Popen(sdk_command(root, job, slot),
                                                     stdout=stdout, stderr=stderr, env=child_environment(), start_new_session=True)
            if not crawl_child and current >= state["nextCrawlAt"]:
                state["nextCrawlAt"] = current + 300
                try:
                    crawl_child = start_crawl(root, job, cache, state, previous_urls)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    save_error(state, "crawler", error)
            due = [lane for lane in INTERVALS if current >= state["nextDue"][lane]]
            if cache.usage()['remainingCents'] < 100:
                for lane in due:
                    state['nextDue'][lane] = paid_deadline
                due = []
            if due:
                lane = min(due, key=lambda name: state["nextDue"][name])
                selection_failed = False
                try:
                    supplementary = sdk_query_supplements(root, job, cache) if job.get('adaptiveTactics') else []
                    item = next_query(lane, plan + supplementary, cache, state)
                except (OSError, ValueError, sqlite3.Error) as error:
                    save_error(state, lane, error)
                    state["nextDue"][lane] = time.time() + 900
                    selection_failed, item = True, None
                if item:
                    # Reserve ownership of the plan entry before sending. The cache owns paid uncertainty.
                    if item.get("parentKey"):
                        state.setdefault("businessSupplementaryQueries", []).append({"parentKey": item["parentKey"], "key": item["key"]})
                    else:
                        state["completedQueries"].append(item["key"])
                    state["nextDue"][lane] = current + intervals[lane]
                    atomic_json(root / "runtime.json", state)
                    try:
                        if not finished_query(item, cache):
                            response = request_before_deadline(cache, lane, item["payload"], paid_deadline)
                            row = cache.get(item["key"])
                            fallback = directory_fallback(item, response)
                            if fallback and time.time() < paid_deadline:
                                fallback_key = request_key(lane, fallback)
                                state.setdefault("supplementaryQueries", []).append({"parentKey": item["key"], "key": fallback_key})
                                atomic_json(root / "runtime.json", state)
                                response = request_before_deadline(cache, lane, fallback, paid_deadline)
                                row = cache.get(fallback_key)
                            failed = provider_is_failure(response, row)
                            state["pathFailures"][lane] = state["pathFailures"].get(lane, 0) + 1 if failed else 0
                            if state["pathFailures"][lane] >= 3:
                                state["nextDue"][lane] = time.time() + 900
                    except (OSError, ValueError, sqlite3.Error) as error:
                        save_error(state, lane, error)
                        state["nextDue"][lane] = time.time() + 900
                elif not selection_failed:
                    state["nextDue"][lane] = paid_deadline
            state["updatedAt"] = now_iso()
            atomic_json(root / "runtime.json", state)
            if time.time() >= state.get("nextExportsAt", 0):
                write_exports(root, cache)
                if job.get("strategy") == "expansion":
                    try:
                        from expansion_metrics import write_metrics
                        measured = write_metrics(root, cache, plan + sdk_query_supplements(root, job, cache))
                        state['tacticFeedback'] = measured['tactics']
                        if job.get("profileRunDir"):
                            from profile_seeds import write_seed_batch
                            write_seed_batch(root, Path(job["profileRunDir"]), cache)
                        if job.get('keywordIndexPath'):
                            from keyword_expansion import SearchIndex
                            index = SearchIndex(Path(job['keywordIndexPath']))
                            try:
                                index.sync_run(root, job['towns'])
                            finally:
                                index.close()
                    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                        save_error(state, "expansion_observation", error)
                state["nextExportsAt"] = time.time() + 60
            runner_snapshot(root, job, cache, state)
            time.sleep(2 if job.get("strategy") == "expansion" else 10)
        state["phase"] = "interrupted" if interrupted else "cleaning"
        atomic_json(root / "runtime.json", state)
        runner_snapshot(root, job, cache, state)
        # Children perform no provider API calls. Gracefully stop only these owned process groups.
        if sdk_child and sdk_child.poll() is None:
            os.killpg(sdk_child.pid, signal.SIGTERM)
        if crawl_child:
            while crawl_child[0].poll() is None and time.time() < original_deadline and not interrupted:
                time.sleep(5)
            if crawl_child[0].poll() is None:
                os.killpg(crawl_child[0].pid, signal.SIGTERM)
                try:
                    crawl_child[0].wait(timeout=15)
                except subprocess.TimeoutExpired:
                    save_error(state, "crawler", ValueError("Graceful stop remains pending"))
            if (crawl_child[1] / "crawl.json").exists():
                finish_crawl(job, crawl_child[1])
        if interrupted:
            runner_snapshot(root, job, cache, state)
            raise SystemExit(1)
        summary = write_exports(root, cache, final=True)
        from clean import clean_exports
        cleaning = clean_exports(root, original_deadline)
        final_report(root, job, cache, state, summary, cleaning)
        state["phase"] = "complete"
        state["completedAt"] = now_iso()
        atomic_json(root / "runtime.json", state)
        runner_snapshot(root, job, cache, state)
        print(json.dumps({"phase": "complete", "runId": job["runId"], "summary": summary}))


def final_report(root, job, cache, state, summary, cleaning):
    sdk = json.loads((root / "sdk/status.json").read_text()) if (root / "sdk/status.json").exists() else {}
    lines = [f"# {job.get('hours', 6)}-hour Buckinghamshire tutor discovery", "", "Original start: " + job["startedAt"],
             "Original deadline: " + job["deadline"], "", "## Results", "",
             f"- {summary['businessIdentities']} deduplicated business/research identities; {summary['research']} research and {summary['held']} held.",
             "- These are research records, not an asserted count of qualified independent 11+ tutors.",
             "- Saved source attribution, business identifiers, no-website records and advertised contact routes.",
             f"- {cleaning['countyListingsResearch']} research identities have a listed Bucks/Milton Keynes address; {cleaning['localSubjectHints']} have 11+ or primary hints.",
             f"- {cleaning['newCountyListingsResearch']} county listing identities have no matching historical evidence in the merged record.",
             f"- {cleaning['outsideRecordedArea']} have only recorded out-of-area addresses; {cleaning['needsLocationReview']} need location review.",
             f"- Postcode lookup failures: {cleaning['postcodeLookupFailures']}. See exports/cleaning-summary.json and the attributed county evidence.",
             "- No emails, messages, domain purchases or tenant imports occurred.", "", "## Usage", "",
             f"- DataForSEO provider-reported new cost: USD {cache.usage()['reportedCostUsd']:.4f}.",
             f"- Conservative window exposure: {cache.usage()['exposureCents']} cents; shared exposure: {cache.usage()['sharedExposureCents']} cents.",
             f"- SDK completed turns: {sdk.get('completedTurns', 0)}; input tokens: {sdk.get('totalInputTokens', 0)}; cached input tokens: {sdk.get('totalCachedInputTokens', 0)}; output tokens: {sdk.get('totalOutputTokens', 0)}.",
             "- Codex used existing ChatGPT OAuth. Token counts do not establish a dollar charge or remaining subscription allowance.",
             "", "## Review", "", "Verify county location, independent ownership, current tuition subjects and advertised contact routes before approval.",
             "Review held records and source-only directories separately. Duplicate branches are retained as place IDs and provenance.",
             "Use exports/local-11-plus-primary-research.csv for the local subject-hint shortlist and exports/bucks-listings-research.csv for wider county listing review. Email claims remain separate from page-extracted contacts.",
             "Read monitor/reports and runtime errors before treating the run as fully healthy.", ""]
    (root / "REPORT.md").write_text("\n".join(lines))


def monitor_health(value, now, stalled):
    if value["phase"] == "complete":
        return "finished", []
    issues = []
    if not value["processLive"]:
        issues.append("Run owner is absent")
    if (now - parse_time(value["observedAt"])).total_seconds() >= 900:
        issues.append("Run checkpoint is stale")
    if stalled:
        issues.append("No successful research progress for thirty minutes")
    if value["budget"].get("uncertainCents", 0):
        issues.append("Paid response charges remain uncertain; identities are held")
    for lane, failures in value.get("consecutivePathFailures", {}).items():
        if failures >= 3:
            if value.get("remainingPlanQueries", {}).get(lane) == 0:
                issues.append(lane + " has no new planned searches; provider failures remain recorded")
            else:
                issues.append(lane + " is cooling down after repeated provider failures")
    last_crawl = value.get("lastCrawlerSuccessAt")
    if any(error.get("lane") == "crawler" and (not last_crawl or parse_time(error["at"]) > parse_time(last_crawl))
           for error in value.get("recentErrors", [])):
        issues.append("Crawler batch failed; no later successful checkpoint is recorded")
    sdk = value.get("sdk") or {}
    if sdk.get("phase") == "failed":
        issues.append("SDK research turn failed; inspect its private events")
    elif sdk.get("phase") == "running" and not sdk.get("threadId") and (
            now - parse_time(sdk["startedAt"])).total_seconds() > 300:
        issues.append("SDK has not started a thread after five minutes")
    elif sdk.get("phase") == "running" and (now - parse_time(sdk["updatedAt"])).total_seconds() > 900:
        issues.append("SDK research events are stale")
    owner_stalled = not value["processLive"] or stalled or (now - parse_time(value["observedAt"])).total_seconds() >= 900
    return "stalled" if owner_stalled else "degraded" if issues else "healthy", issues


def watch(root):
    if not (root / "job.json").exists() or not (root / "status.json").exists():
        labels, interval = launchd_settings(json.loads((root / "manifest.json").read_text()))
        now = datetime.datetime.now(UTC)
        report = {"checkedAt": now.isoformat(), "health": "starting", "action": "none",
                  "issues": ["Waiting for the launcher's first runtime checkpoint"], "recoveries": 0,
                  "monitorIntervalSeconds": interval, "launchdLabels": labels}
        atomic_json(root / "monitor/reports" / (now.strftime("%Y%m%dT%H%M%SZ") + ".json"), report)
        atomic_json(root / "monitor/latest.json", report)
        print(json.dumps(report))
        return
    value = status(root)
    job = json.loads((root / "job.json").read_text())
    labels, interval = launchd_settings(job)
    now = datetime.datetime.now(UTC)
    expired = now >= parse_time(job["deadline"])
    terminal = value["phase"] == "complete"
    previous_path = root / "monitor/latest.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    progress = {"successfulQueries": value["budget"]["succeeded"],
                "completedSdkTurns": value["sdk"].get("completedTurns", 0),
                "crawlerEvidencePages": value["exports"].get("newCrawlerEvidencePages", 0),
                "businesses": value["exports"].get("businessIdentities", 0)}
    changed = previous.get("progress") != progress
    last_progress = now.isoformat() if changed else previous["lastProgressAt"]
    stale_progress = (now - parse_time(last_progress)).total_seconds() > 1800
    health, issues = monitor_health(value, now, stale_progress)
    report = {"checkedAt": now.isoformat(), "health": health, "expired": expired, "snapshot": value, "action": "none",
              "progress": progress, "lastProgressAt": last_progress, "recoveries": previous.get("recoveries", 0),
              "monitorIntervalSeconds": interval, "launchdLabels": labels}
    report["issues"] = issues
    if not expired and not terminal and not value["processLive"] and report["recoveries"] < 3:
        # launchd owns restart policy. Kickstart only this registered job; never kill an owner or replay a paid request.
        result = subprocess.run(["/bin/launchctl", "kickstart", f"gui/{os.getuid()}/{labels['run']}"], capture_output=True, timeout=10)
        report["action"] = "restart_requested" if result.returncode == 0 else "registered_restart_unavailable"
        report["recoveries"] += 1
    atomic_json(root / "monitor/reports" / (now.strftime("%Y%m%dT%H%M%SZ") + ".json"), report)
    atomic_json(root / "monitor/latest.json", report)
    print(json.dumps(report))
    if terminal or expired:
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{labels['watch']}"], capture_output=True, timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status", "watch"])
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        run(args.run_dir)
    elif args.command == "status":
        print(json.dumps(status(args.run_dir)))
    else:
        watch(args.run_dir)
