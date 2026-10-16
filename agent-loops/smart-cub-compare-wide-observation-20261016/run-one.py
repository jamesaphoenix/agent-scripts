"""Prepared one-shot follow-up; execution requires a separate explicit approval."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def preflight():
    manifest = json.loads((HERE / 'handoff.json').read_text())
    for item in manifest['implementationFiles']:
        if sha(HERE / item['path']) != item['sha256']:
            raise ValueError('Prepared helper changed: ' + item['path'])
    policy = module('followup_policy', HERE / 'helpers/variant-policy.py')
    if sha(policy.OLD_REQUEST) != policy.OLD_SHA:
        raise ValueError('Original completed advisory changed')
    old = json.loads(policy.OLD_REQUEST.read_text())
    ledger = json.loads(policy.LEDGER.read_text())
    reservation = ledger['budget']['reservations'][old['reservation_id']]
    if old['state'] != 'completed_advisory' or old['advisory']['observed_complete'] is not False:
        raise ValueError('Original job is not the known terminal incomplete observation')
    if reservation['state'] != 'settled' or reservation['actual_cents'] != 3:
        raise ValueError('Original paid state is unresolved or changed')
    for item in manifest['protectedOriginalFiles']:
        if sha(item['path']) != item['sha256']:
            raise ValueError('Protected original source or evidence changed')
    guard_path = policy.RUN / 'workflow/review_admission.py'
    if sha(guard_path) != manifest['guardSha256']:
        raise ValueError('Shared admission guard changed')
    if ledger['budget']['cap_cents'] != 10000:
        raise ValueError('Original USD100 cap changed')
    return policy, manifest, guard_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    policy, manifest, guard_path = preflight()
    if args.check:
        print(json.dumps({'state': 'prepared_checked_no_provider_calls', 'id': policy.IDENTITY,
                          'aspect': 'wide', 'outputSha256': policy.OUTPUT, 'maximumCents': 11}))
        return
    if not os.uname().nodename.lower().split('.')[0] == 'jamessmacstudio':
        raise ValueError('Mac Studio required')
    status = policy.RUN / 'workflow/compare-wide-followup-v1-status.json'
    # Exclusive creation refuses any accidental repeat, including unknown paid state.
    with status.open('x') as stream:
        json.dump({'state': 'admission_pending', 'pid': os.getpid(), 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'publicationApproval': False, 'maximumCents': 11}, stream)
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    guard = module('followup_guard', guard_path)
    intent = {'id': policy.IDENTITY, 'aspect': 'wide', 'outputSha256': policy.OUTPUT}
    with guard.admission(policy.RUN, family='corrected', intent=intent) as lease:
        preflight()
        command = [str(policy.RUN / '.venv/bin/python'), '-B', str(HERE / 'helpers/review-media.py'),
                   'model', '--root', str(policy.ROOT), '--ledger', str(policy.LEDGER),
                   '--evidence-root', str(policy.EVIDENCE), '--ids', policy.IDENTITY,
                   '--aspect', 'wide', '--max-requests', '1']
        log = policy.RUN / 'evidence/compare-wide-followup-v1.log'
        with log.open('xb') as stream:
            child = lease.popen(command, stdout=stream, stderr=stream)
            code = child.wait()
        status.write_text(json.dumps({'state': 'completed_requires_individual_review' if code == 0 else 'halted_requires_reconciliation',
                                     'exitCode': code, 'publicationApproval': False,
                                     'log': str(log), 'logSha256': sha(log)}, indent=2) + '\n')
        if code:
            sys.exit(code)


if __name__ == '__main__':
    main()
