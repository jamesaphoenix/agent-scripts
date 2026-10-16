#!/usr/bin/env python3
"""Prepare a request-anchored campaign without releasing old uncertain charges."""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tomllib
import re

from configuration import campaign_window, launchd_settings
from discovery import PaidCache, atomic_json, file_hash, request_key
from directory_profiles import admitted_url
from keyword_expansion import SearchIndex
from prepare_profile_followon import prepare as prepare_profiles
from tactics import campaign_plan
from lead_scope import inherit_scope


def archive_history(history):
    """Only sealed paid owners are reusable. An unverified request stays blocked."""
    archives, exposure = [], 0
    for root in sorted(history.glob('tutor-lead-pathways-*')):
        path = root / 'paid.sqlite'
        if not path.exists():
            continue
        if json.loads((root / 'runtime.json').read_text()).get('phase') != 'complete':
            raise ValueError('Historical paid owner is not terminal: ' + root.name)
        with (root / 'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            db.row_factory = sqlite3.Row
            try:
                db.execute('BEGIN')
                rows = [dict(row) for row in db.execute('SELECT * FROM requests')]
                prior = db.execute('SELECT prior FROM settings WHERE id=1').fetchone()[0]
            finally:
                db.close()
        spent = sum(row['cost_cents'] + (row['reservation'] if row['status'] in ('uncertain', 'submitting') else 0)
                    for row in rows if not row['imported'])
        exposure = max(exposure, prior + spent)
        saved_rows = []
        for row in rows:
            payload = json.loads(row['payload'])
            if row['key'] != request_key(row['lane'], payload):
                raise ValueError('Historical request hash changed')
            envelope = None
            if row['receipt']:
                receipt = root / 'receipts' / (row['key'] + '.json')
                if file_hash(receipt) != row['receipt_hash']:
                    raise ValueError('Historical paid receipt changed')
                saved = json.loads(receipt.read_text())
                if saved['payload'] != payload or saved['key'] != row['key'] or saved['lane'] != row['lane']:
                    raise ValueError('Historical receipt identity mismatch')
                envelope = saved['response']
                tasks = envelope.get('tasks') or []
                if len(tasks) != 1 or tasks[0].get('id') != row['task_id']:
                    raise ValueError('Historical provider identity changed')
            elif row['status'] not in ('uncertain', 'submitting'):
                raise ValueError('Settled historical request has no receipt')
            saved_rows.append((row['lane'], payload, envelope, row['error']))
        archives.append((root, saved_rows))
    return exposure, archives


def seed_batch(root, urls):
    import hashlib
    urls = sorted({url for candidate in urls if (url := admitted_url(candidate))})
    batches = []
    for offset in range(0, len(urls), 5000):
        value = {'urls': urls[offset:offset + 5000]}
        digest = hashlib.sha256((json.dumps(value, indent=2) + '\n').encode()).hexdigest()
        path = root / 'seed-batches' / (digest + '.json')
        atomic_json(path, value)
        batches.append(path)
    return batches


def prepare(root, authorization, history, profile_parent, profile_root, identity_baseline,
            focused_root, towns_file, codex, crawler, extension_parent=None):
    root, history = root.resolve(), history.resolve()
    if (root / 'manifest.json').exists() or profile_root.exists():
        raise ValueError('Existing stages must be resumed, never prepared again')
    auth = json.loads(authorization.read_text())
    window = {'hours': 12, 'authorizedStartedAt': auth['requestedAt'], 'authorizedDeadline': auth['deadline']}
    started, deadline, _ = campaign_window(window)
    prior, archives = archive_history(history)
    inherited_reserve = 0
    parent_job = None
    if extension_parent:
        parent_job = json.loads((extension_parent / 'manifest.json').read_text())
        if (file_hash(extension_parent / 'manifest.json') != auth['parentManifestSha256']
                or auth['requestedAt'] != parent_job['authorizedDeadline']
                or parent_job['reservedDecisionsLifetimeCents'] != 300
                or parent_job['priorExposureCents'] != parent_job['priorDfsExposureCents'] + 300
                or parent_job['sharedCapCents'] != 10000):
            raise ValueError('Extension changed its parent window or shared reserve')
        inherited_reserve = 300
    if prior - inherited_reserve != auth['priorDfsExposureCents'] or auth['campaignBudgetCapUsd'] != 100:
        raise ValueError('Paid history no longer matches the authorised budget baseline')
    classifier_reserve = auth['reservedDecisionsLifetimeCents']
    budget = auth['maximumNewDfsCents']
    floor = prior + classifier_reserve - inherited_reserve
    if classifier_reserve != 300 or type(budget) is not int or budget < 0 or floor + budget > 10000:
        raise ValueError('Shared spending and classifier reserve exceed USD100')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    inherited_scope = inherit_scope(extension_parent, root) if extension_parent else None
    if inherited_scope:
        atomic_json(root / 'inherited-subject-scope.json', inherited_scope)
    atomic_json(root / 'authorization.json', auth)
    cache = PaidCache(root, budget, floor)
    try:
        for previous, rows in archives:
            for lane, payload, response, error in rows:
                if response is not None:
                    cache.import_receipt(lane, payload, response)
                else:
                    cache.import_unsettled(lane, payload, 'Inherited hold from ' + previous.name + ': ' + (error or 'unverified response'))
        imported = cache.usage()
        towns = json.loads(towns_file.read_text())
        index_path = history / 'keyword-search.sqlite'
        index = SearchIndex(index_path)
        try:
            plan = campaign_plan(towns)
            index.add_queries(plan, origin='plan:' + str(root))
            plan = [item for item in index.unsearched(plan) if not cache.get(item['key'])]
            seen = {item['key'] for item in plan}
            approved = {town['name'] for town in towns}
            for item in index.pending_plan(5000):
                if item['generation'] > 0 and item['town'] in approved and item['key'] not in seen and not cache.get(item['key']):
                    plan.append(item)
                    seen.add(item['key'])
        finally:
            index.close()
        if not plan and not extension_parent:
            raise ValueError('No unsearched, non-held query identities remain')
        if extension_parent:
            plan.sort(key=lambda item: 0 if re.search(r'11(?:%2b|[ +])|eleven|entrance|reasoning',
                item['payload'].get('keyword', ''), re.I) else 1)
        atomic_json(root / 'plan.json', plan)
    finally:
        cache.db.close()
    config = tomllib.loads((Path.home() / '.codex/config.toml').read_text())
    source_root = Path(__file__).resolve().parents[2]
    source_sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=source_root, capture_output=True,
                                text=True, check=True, timeout=10).stdout.strip()
    suffix = root.name.removeprefix('tutor-lead-pathways-')
    manifest = dict(window, runId=root.name, strategy='expansion', budgetCents=budget,
        priorExposureCents=floor, priorDfsExposureCents=prior - inherited_reserve,
        reservedDecisionsLifetimeCents=classifier_reserve, sharedCapCents=10000,
        planSha256=file_hash(root / 'plan.json'), authorizationSha256=file_hash(root / 'authorization.json'),
        sourceSha=source_sha, towns=towns, codexBinary=str(codex.resolve()), codexModel=config.get('model'),
        nodeBinary=shutil.which('node'), disabledMcpServers=list(config.get('mcp_servers', {})),
        crawlerBinary=str(crawler.resolve()), crawlerSha256=file_hash(crawler),
        historyRoot=str(history), historicalEvidenceRoots=[str(previous) for previous, _ in archives],
        importedHistoricalReceipts=imported['importedReceipts'], importedUnsettledRequests=imported['importedUnsettledRequests'],
        maximumSdkTurns=24, sdkTokenCeiling=4000000, codexThreadMode='isolated', sdkTownOffset=0,
        freePageBudget=20000, crawlLimits={'depth': 2, 'branchSites': 15, 'sites': 300,
            'batchPages': 1000, 'pagesPerSite': 8, 'workers': 4, 'rootsPerBatch': 30},
        requestIntervals={'maps': 90, 'business': 180, 'organic': 90, 'directories': 90},
        adaptiveTactics=True, keywordIndexPath=str(index_path), profileRunDir=str(profile_root.resolve()),
        focusedRoots=[str(focused_root.resolve()), str(history / 'tutor-focused-20261013-12h')],
        focusedParent=str(focused_root.resolve()), focusedContinuation=str(history / 'tutor-focused-20261013-12h'),
        focusedHistory=str(archives[-1][0]),
        identityBaselineCsv=str(identity_baseline.with_suffix('.csv').resolve()),
        pathways=['maps', 'business', 'organic', 'directories', 'codex_oauth'],
        monitorIntervalSeconds=900, launchdLabels={'run': 'com.jud.tutor-lead-pathways-' + suffix,
            'watch': 'com.jud.tutor-lead-pathways-watch-' + suffix},
        delivery='Public professional research only; no external outreach')
    launchd_settings(manifest)
    if extension_parent:
        from configuration import focused_roots
        manifest.update(focusedRoots=[str(p) for p in focused_roots(parent_job)],
            classifierContinuation='disabled_shared_allowance_exhausted',
            previousCampaignRoot=str(extension_parent.resolve()),
            previousAdditionalContactsSha256=file_hash(extension_parent / 'exports/additional-contacts.json'),
            researchPriority='11_plus_and_private_academic',
            sdkTownOffset=parent_job.get('sdkTownOffset', 0) + 4 * parent_job['maximumSdkTurns'],
            extensionRequestSha256=auth['extensionRequestSha256'])
    atomic_json(root / 'manifest.json', manifest)
    atomic_json(root / 'towns.json', towns)
    # Snapshot all existing evidence, including unaudited pages. Reclassifying
    # something already collected does not turn it into a new contact.
    baseline = json.loads((extension_parent / 'baseline/contacts.json' if extension_parent else identity_baseline).read_text())
    old_businesses = json.loads((archives[-1][0] / 'exports/businesses.json').read_text())
    for row in ([] if extension_parent else old_businesses):
        baseline.append({'canonicalId': 'business:' + (row.get('website') or row.get('name') or '') + ':' + (row.get('phone') or ''),
            'name': row.get('name') or '', 'sourceUrls': [row['website']] if row.get('website') else [], 'outreachEligible': False})
    focused = json.loads((focused_root / 'profile-candidates-not-in-baseline.json').read_text())
    if not extension_parent:
        baseline.extend({'canonicalId': 'page:' + row['url'], 'name': row.get('title') or '',
                         'sourceUrls': [row['url']], 'outreachEligible': False} for row in focused)
    atomic_json(root / 'baseline/contacts.json', baseline)
    atomic_json(root / 'baseline/inventory.json', {'auditedIdentities': len(json.loads(identity_baseline.read_text())),
        'businessResearchRows': len(old_businesses), 'previousUnauditedProfiles': len(focused),
        'identityBaselineSha256': file_hash(identity_baseline), 'contactsSha256': file_hash(root / 'baseline/contacts.json'),
        'capturedAfterRequestedStart': True, 'note': 'Conservative baseline includes setup-time discoveries as already known.'})
    temporary_seeds = seed_batch(root, [row['url'] for row in focused])
    prepare_profiles(profile_parent, profile_root, 12, 12000, 5000,
        temporary_seeds[0] if temporary_seeds else None, root, requested_start=started,
        cumulative_ceiling=32000 if extension_parent else 20000, allow_empty=bool(extension_parent))
    profile_job = json.loads((profile_root / 'job.json').read_text())
    profile_job['continueUntilDeadline'] = True
    if extension_parent:
        profile_job.update(additionalWindowAuthorization=str((root / 'authorization.json').resolve()),
            additionalWindowAuthorizationSha256=file_hash(root / 'authorization.json'))
    atomic_json(profile_root / 'job.json', profile_job)
    for path in temporary_seeds[1:]:
        shutil.copy2(path, profile_root / 'seed-batches' / path.name)
    atomic_json(profile_root / 'review/stage-start-baseline.json', baseline)
    if extension_parent:
        previous = extension_parent / 'sdk/status.json'
        if previous.exists():
            sdk = json.loads(previous.read_text())
            if sdk.get('phase') == 'running':
                # Preserve known usage and hold this lane if the old turn never
                # recorded a final receipt. Public and paid discovery can continue.
                manifest.update(maximumSdkTurns=0, sdkUsageInheritedUnsettled=True)
                atomic_json(root / 'manifest.json', manifest)
            atomic_json(root / 'sdk/status.json', sdk)
            atomic_json(root / 'sdk/inherited-usage.json', {'source': str(previous),
                'sha256': file_hash(previous), 'tokenCeiling': manifest['sdkTokenCeiling'],
                'totalInputTokens': sdk.get('totalInputTokens', 0),
                'totalOutputTokens': sdk.get('totalOutputTokens', 0)})
        else:
            manifest.update(maximumSdkTurns=0, sdkUsageInheritedUnsettled=True)
            atomic_json(root / 'manifest.json', manifest)
        atomic_json(root / 'preparation-complete.json', {
            'manifestSha256': file_hash(root / 'manifest.json'),
            'profileJobSha256': file_hash(profile_root / 'job.json'),
            'authorizationSha256': file_hash(root / 'authorization.json'),
            'extensionRequestSha256': auth['extensionRequestSha256']})
    return {'runId': root.name, 'deadline': deadline.isoformat(), 'newQueryPlan': len(plan),
        'maximumNewDfsCents': budget, 'reservedClassifierCents': classifier_reserve,
        'importedReceipts': imported['importedReceipts'], 'inheritedHeldRequestKeys': imported['importedUnsettledRequests'],
        'sourceSha': source_sha, 'baselineInventory': str(root / 'baseline/inventory.json')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('run-dir', 'authorization', 'history-root', 'profile-parent', 'profile-run-dir',
                 'identity-baseline', 'focused-root', 'towns', 'codex', 'crawler'):
        parser.add_argument('--' + name, type=Path, required=True)
    a = parser.parse_args()
    print(json.dumps(prepare(a.run_dir, a.authorization, a.history_root, a.profile_parent,
        a.profile_run_dir, a.identity_baseline, a.focused_root, a.towns, a.codex, a.crawler)))
