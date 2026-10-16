"""A stricter shared spending ceiling over existing immutable provider banks."""
from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3

NANO_PER_CENT = 10_000_000
INSTRUCTION = 'Limit the remaining budget to $15 i n eed to save funds plz.'


def authorization(path, digest=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Spending authorization cannot be a symlink')
    raw = path.read_bytes()
    if len(raw) > 16000 or digest and hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Spending authorization changed')
    value = json.loads(raw)
    baseline = value['baseline']
    if (value.get('instruction') != INSTRUCTION or value.get('maximumFurtherSpendCents') != 1500
            or value.get('newMoneyAuthorizedCents') != 0
            or set(value.get('includes', [])) != {'DataForSEO', 'Decisions classification'}
            or any(type(baseline.get(k)) is not int or baseline[k] < 0 for k in
                   ('paidSpentCents', 'paidHeldCents', 'decisionsSpentNano', 'decisionsHeldNano'))
            or Path(baseline['paidLedger']).resolve() == Path(baseline['decisionsLedger']).resolve()):
        raise ValueError('Invalid shared USD15 spending reduction')
    return value, hashlib.sha256(raw).hexdigest()


def install(path):
    """Attach the same sealed instruction without changing either original cap."""
    path = Path(path).resolve()
    value, digest = authorization(path)
    config = json.dumps({'authorizationPath': str(path), 'authorizationSha256': digest}, sort_keys=True)
    for name in ('paidLedger', 'decisionsLedger'):
        ledger = Path(value['baseline'][name])
        with closing(sqlite3.connect(ledger.as_uri() + '?mode=rw', uri=True, timeout=30)) as db:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS spend_limit (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL)')
                old = db.execute('SELECT config FROM spend_limit WHERE id=1').fetchone()
                if old and old[0] != config:
                    raise ValueError('Existing spending reduction cannot be reset')
                db.execute('INSERT OR IGNORE INTO spend_limit VALUES(1,?)', (config,))


def policy(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spend_limit'").fetchone():
        return None
    row = db.execute('SELECT config FROM spend_limit WHERE id=1').fetchone()
    if row is None:
        raise ValueError('Missing sealed spending reduction')
    config = json.loads(row[0])
    path = Path(config['authorizationPath'])
    value, _ = authorization(path, config['authorizationSha256'])
    current = Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve()
    if current not in {Path(value['baseline'][k]).resolve() for k in ('paidLedger', 'decisionsLedger')}:
        raise ValueError('Spending reduction belongs to different provider ledgers')
    return path, value, row[0]


def remaining(value, expected_config):
    baseline = value['baseline']
    with closing(sqlite3.connect(Path(baseline['paidLedger']).as_uri() + '?mode=ro', uri=True, timeout=30)) as paid, \
            closing(sqlite3.connect(Path(baseline['decisionsLedger']).as_uri() + '?mode=ro', uri=True, timeout=30)) as decisions:
        for db in (paid, decisions):
            attached = db.execute('SELECT config FROM spend_limit WHERE id=1').fetchone()
            if attached is None or attached[0] != expected_config:
                raise ValueError('Provider banks do not share the same spending reduction')
        paid_used = paid.execute("SELECT coalesce(sum(cost_cents + CASE WHEN status IN ('submitting','uncertain') THEN reservation ELSE 0 END),0) FROM requests WHERE imported=0").fetchone()[0]
        decisions_used = decisions.execute("SELECT coalesce(sum(CASE WHEN state='succeeded' THEN spent_nano ELSE reservation_nano END),0) FROM decision_requests").fetchone()[0]
    used = max(0, paid_used - baseline['paidSpentCents']) * NANO_PER_CENT
    used += max(0, decisions_used - baseline['decisionsSpentNano'])
    return max(0, value['maximumFurtherSpendCents'] * NANO_PER_CENT - used)


def summary(db):
    attached = policy(db)
    if attached is None:
        return None
    _, value, config = attached
    return {'ceilingUsd': 15, 'remainingUsd': remaining(value, config) / 1e9,
            'requestedAt': value['requestedAt'], 'sharedAcross': value['includes']}


@contextmanager
def admission(db):
    """Serialize both providers' reservations before their own SQL transactions."""
    attached = policy(db)
    if attached is None:
        yield None
        return
    path, value, config = attached
    with (path.parent / 'admission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Check again after acquiring the shared lock; settlement can only
        # reduce the exposure established by a conservative reservation.
        authorization(path, json.loads(config)['authorizationSha256'])
        yield remaining(value, config)
