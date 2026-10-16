"""Reconcile saved provider charges offline without changing any paid ledger."""
import argparse
from contextlib import ExitStack, closing
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

from configuration import loaded_source_sha
from discovery import atomic_json, file_hash, now_iso, request_key

NANO_PER_CENT = 10_000_000
CAP_CENTS = 10000


def owner_status(label):
    result = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{label}'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        return 'running' if re.search(r'pid = [1-9]\d*', result.stdout) else 'stopped'
    if 'Could not find service' in result.stderr:
        return 'missing'
    raise ValueError('Cannot verify paid owner status: ' + label)


def fingerprint(root):
    wal = root / 'paid.sqlite-wal'
    return {'databaseSha256': file_hash(root / 'paid.sqlite'),
            'walSha256': file_hash(wal) if wal.exists() and wal.stat().st_size else None,
            'manifestSha256': file_hash(root / 'manifest.json'),
            'runtimeSha256': file_hash(root / 'runtime.json')}


def money(value):
    try:
        if isinstance(value, bool):
            raise ValueError('Invalid provider cost')
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= 100:
            raise ValueError('Invalid provider cost')
        return amount
    except (InvalidOperation, TypeError) as error:
        raise ValueError('Invalid provider cost') from error


def receipt(root, row):
    path = root / 'receipts' / (row['key'] + '.json')
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('A paid receipt must not be a symlink')
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('Paid receipt exceeds its size bound')
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != row['receipt_hash']:
        raise ValueError('Paid receipt hash changed')
    saved = json.loads(raw)
    if (saved['key'] != row['key'] or saved['lane'] != row['lane']
            or saved['payload'] != json.loads(row['payload'])
            or request_key(saved['lane'], saved['payload']) != row['key']):
        raise ValueError('Paid receipt identity changed')
    # Read monetary JSON numbers as decimals, independently of payload parsing.
    envelope = json.loads(raw, parse_float=Decimal)['response']
    tasks = envelope.get('tasks') or []
    if len(tasks) != 1 or not tasks[0].get('id') or tasks[0]['id'] != row['task_id']:
        raise ValueError('Paid task identity changed')
    amount = money(envelope.get('cost'))
    if amount != money(tasks[0].get('cost')):
        raise ValueError('Provider envelope and task cost mismatch')
    expected = ('succeeded' if envelope.get('status_code') == 20000
                and tasks[0].get('status_code') in (20000, 40106) else 'failed')
    if row['status'] != expected:
        raise ValueError('Paid receipt outcome changed')
    if not row['imported'] and (money(row['cost_usd']) != amount
            or row['cost_cents'] != int((amount * 100).to_integral_value(rounding=ROUND_CEILING))):
        raise ValueError('Settled ledger cost differs from provider receipt')
    nanos = int((amount * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))
    return {'key': row['key'], 'taskId': row['task_id'], 'lane': row['lane'],
            'providerCostNano': nanos, 'receipt': str(path), 'receiptSha256': digest}


def audit(history, latest, initial, owner_probe=owner_status):
    history, latest, initial = map(lambda p: Path(p).resolve(), (history, latest, initial))
    roots = sorted(p for p in history.glob('tutor-lead-pathways-*') if (p / 'paid.sqlite').exists())
    if latest not in roots or initial not in roots:
        raise ValueError('Both budget owners must belong to the complete source history')
    if initial != roots[0]:
        raise ValueError('Initial baseline must come from the first historical owner')
    snapshots, settings, manifests, rows, owners = {}, {}, {}, {}, {}
    with ExitStack() as stack:
        for root in roots:
            if root.is_symlink() or (root / 'paid.sqlite').is_symlink():
                raise ValueError('A paid source must not be a symlink')
            lock = stack.enter_context((root / 'run.lock').open('rb'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for root in roots:
            runtime = json.loads((root / 'runtime.json').read_text())
            manifest = json.loads((root / 'manifest.json').read_text())
            if runtime.get('phase') != 'complete':
                raise ValueError('Historical paid owner is not terminal: ' + root.name)
            label = (manifest.get('launchdLabels') or {}).get('run') or 'com.jud.' + root.name
            if not re.fullmatch(r'com\.jud\.[A-Za-z0-9._-]+', label):
                raise ValueError('Invalid paid owner label')
            status = owner_probe(label)
            if status not in ('stopped', 'missing'):
                raise ValueError('Historical paid owner is running: ' + label)
            owners[root] = {'label': label, 'status': status}
            snapshots[root] = fingerprint(root)
            with closing(sqlite3.connect((root / 'paid.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN')
                settings[root] = dict(db.execute('SELECT * FROM settings WHERE id=1').fetchone())
                rows[root] = [dict(r) for r in db.execute('SELECT * FROM requests ORDER BY key')]
            value = settings[root]
            if (manifest.get('sharedCapCents') != CAP_CENTS or manifest.get('priorExposureCents') != value['prior']
                    or value['prior'] < 0 or value['budget'] < 0 or value['prior'] + value['budget'] > CAP_CENTS):
                raise ValueError('Historical budget configuration changed')
            manifests[root] = manifest
        classification = manifests[latest].get('reservedDecisionsLifetimeCents')
        if classification != 300:
            raise ValueError('Original classification reserve must remain intact')
        current = {r['key']: r for r in rows[latest]}
        historical_keys = {r['key'] for records in rows.values() for r in records}
        if historical_keys - current.keys():
            raise ValueError('Latest owner omits historical requests')
        verified, paid_tasks, held, origins = {}, {}, [], set()
        for root in roots:
            for row in rows[root]:
                if (row['imported'] not in (0, 1) or row['status'] not in
                        ('succeeded', 'failed', 'uncertain', 'submitting')
                        or request_key(row['lane'], json.loads(row['payload'])) != row['key']):
                    raise ValueError('Historical request identity or state changed')
                if row['imported'] and (row['cost_cents'] or money(row['cost_usd']) or row['reservation']):
                    raise ValueError('Imported request has an unaccounted charge or reservation')
                if row['status'] in ('uncertain', 'submitting'):
                    if row['receipt'] or row['task_id'] or row['cost_cents'] or money(row['cost_usd']):
                        raise ValueError('Uncertain request needs separate provider reconciliation')
                    if not row['imported']:
                        if type(row['reservation']) is not int or row['reservation'] <= 0:
                            raise ValueError('Missing originating reservation')
                        held.append({'key': row['key'], 'root': str(root), 'lane': row['lane'],
                                     'reservationCents': row['reservation'], 'status': row['status']})
                        origins.add(row['key'])
                    continue
                proof = receipt(root, row)
                old = verified.get(row['key'])
                if old and (old['taskId'], old['providerCostNano']) != (proof['taskId'], proof['providerCostNano']):
                    raise ValueError('Historical key has conflicting paid submissions')
                if proof['taskId'] in paid_tasks and paid_tasks[proof['taskId']] != row['key']:
                    raise ValueError('Provider task has conflicting paid identities')
                paid_tasks[proof['taskId']] = row['key']
                verified[row['key']] = proof
        unknown = {key for key, row in current.items() if row['status'] in ('uncertain', 'submitting')}
        if unknown != origins:
            raise ValueError('Uncertain request is missing its originating reservation or resolution proof')
        if unknown & verified.keys():
            raise ValueError('A previously uncertain request needs explicit resolution proof')
        provider = sum(r['providerCostNano'] for r in verified.values())
        reservations = sum(r['reservationCents'] for r in held)
        baseline = settings[initial]['prior']
        exposure = provider + (reservations + classification + baseline) * NANO_PER_CENT
        prior_cents = (exposure + NANO_PER_CENT - 1) // NANO_PER_CENT
        if prior_cents > CAP_CENTS:
            raise ValueError('Reconciled exposure exceeds the original USD100 cap')
        for root in roots:
            if fingerprint(root) != snapshots[root]:
                raise ValueError('Paid source changed during the offline audit')
        own = [r for r in rows[latest] if not r['imported']]
        current_exposure = sum(r['cost_cents'] + (r['reservation']
            if r['status'] in ('uncertain', 'submitting') else 0) for r in own)
        return {'schemaVersion': 1, 'observedAt': now_iso(), 'sourceSha': loaded_source_sha(),
            'state': 'offline_audit_only_not_applied', 'dispatchEnabled': False, 'paidCalls': 0,
            'ledgerMutations': 0, 'uncertainReservationsReleased': 0,
            'originalDiscoveryCapCents': CAP_CENTS, 'verifiedSettledRequests': len(verified),
            'verifiedSettledProviderCostNano': provider, 'uncertainRequests': len(unknown),
            'uncertainOriginAttempts': len(held), 'uncertainReservationsCents': reservations,
            'initialBaselineBufferCents': baseline, 'originalClassificationReserveCents': classification,
            'reconciledConservativeExposureNano': exposure, 'proposedPriorExposureCents': prior_cents,
            'potentialHeadroomWholeCents': CAP_CENTS - prior_cents,
            'currentConservativeHeadroomCents': settings[latest]['budget'] - current_exposure,
            'requiresNewVerifiedBudgetOwner': True, 'requiresFreshWindowApproval': True,
            'sourceLedgers': [dict(path=str(root / 'paid.sqlite'), settings=settings[root],
                owner=owners[root], **snapshots[root]) for root in roots],
            'uncertainOrigins': held, 'receipts': list(verified.values())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('history-root', 'latest-run', 'initial-run'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.history_root, args.latest_run, args.initial_run)
    atomic_json(args.latest_run / 'verification/paid-budget-reconciliation.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('sourceLedgers', 'receipts', 'uncertainOrigins')}))
