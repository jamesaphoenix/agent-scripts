"""Parse saved 1Password CLI quota metadata without making another request."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path

from discovery import atomic_json


def account_quota(payload):
    rows = payload if isinstance(payload, list) else payload.get('quotas') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError('Unrecognised quota response; availability remains unknown')
    accounts = [row for row in rows if row.get('type') == 'account' and row.get('action') == 'read_write']
    if len(accounts) != 1:
        raise ValueError('Expected exactly one account read/write quota')
    row = accounts[0]
    fields = ('limit', 'used', 'remaining', 'reset')
    if any(type(row.get(key)) is not int or row[key] < 0 for key in fields):
        raise ValueError('Quota counters must be nonnegative integers')
    if row['used'] + row['remaining'] != row['limit']:
        raise ValueError('Inconsistent account quota counters')
    return {'status': 'available' if row['remaining'] > 0 else 'exhausted',
            'accountReadWriteRemaining': row['remaining'], 'accountReadWriteLimit': row['limit'],
            'accountReadWriteUsed': row['used'], 'accountResetSeconds': row['reset']}


def repair_report(source, output, observed_at):
    """Keep the failed receipt and replace it using the saved metadata only."""
    observed = datetime.datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
    if observed.tzinfo is None:
        raise ValueError('The metadata observation requires a timezone')
    raw = source.read_bytes()
    if len(raw) > 65536:
        raise ValueError('Quota metadata exceeds its bound')
    result = account_quota(json.loads(raw))
    prior_raw = output.read_bytes()
    prior = json.loads(prior_raw)
    if prior.get('status') != 'error' or prior.get('secretReadAttempted') is not False:
        raise ValueError('Only a failed metadata parser receipt may be repaired')
    prior_hash = hashlib.sha256(prior_raw).hexdigest()
    preserved = output.with_name(output.stem + '.before-parser-repair-' + prior_hash + '.json')
    with preserved.open('xb') as stream:
        stream.write(prior_raw)
    report = {**prior, **result, 'observedAt': observed.isoformat(),
              'accountReadWriteRemainingBasis': 'Parsed account read/write counters from saved CLI metadata',
              'source': 'One replacement metadata observation after a local parser failure; no polling',
              'result': 'Saved CLI quota metadata parsed successfully; no secret read performed by this parser',
              'metadataRequestAttempts': prior.get('metadataRequestAttempts', 1) + 1,
              'oneCheckOnly': False, 'parserRepairReplacementCheckOnly': True,
              'responsePath': str(source), 'responseSha256': hashlib.sha256(raw).hexdigest(),
              'previousReceiptPath': str(preserved), 'previousReceiptSha256': prior_hash,
              'secretReadAttempted': False, 'retryAllowed': False}
    atomic_json(output, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--observed-at', required=True)
    args = parser.parse_args()
    report = repair_report(args.input, args.output, args.observed_at)
    print(json.dumps({key: report[key] for key in ('status', 'accountReadWriteRemaining', 'observedAt')}))
