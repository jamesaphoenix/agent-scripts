"""Validate the explicit task-owned marketing revision handoff, without provider calls."""
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validated_helpers(data, run, app):
    helper = Path(data.get('reviewHelperRoot', ''))
    if not helper.is_absolute() or helper.is_symlink() or not helper.resolve().is_relative_to((run / 'workflow').resolve()):
        raise ValueError('Family helpers must stay in the isolated task workflow')
    expected = data.get('reviewHelperHashes', {})
    if not {'review-media.py', 'review-ledger.py', 'prepare-review.py'}.issubset(expected):
        raise ValueError('Family helper implementation hashes are required')
    for name, digest in expected.items():
        target = (helper / name).resolve()
        if Path(name).name != name or not target.is_relative_to(helper.resolve()) or sha(target) != digest:
            raise ValueError('Protected family helper changed: ' + name)
    if expected['review-media.py'] != data['protectedSourceHashes']['scripts/review-media.py']:
        raise ValueError('Model helper must preserve the exact review implementation and cache identity')
    return helper


def model_command(data, run, app, identity, aspect):
    helper = validated_helpers(data, run, app)
    return [str(run / '.venv/bin/python'), '-B', str(helper / 'review-media.py'), 'model',
            '--root', str(app), '--ids', identity, '--aspect', aspect, '--max-requests', '1',
            '--ledger', str(run / 'ledger/media-review-ledger.json'),
            '--evidence-root', data['reviewEvidenceRoot']]


def load_revision(path, run):
    path = path.resolve()
    if not path.is_relative_to(run.resolve()):
        raise ValueError('Revision config must stay under the task run root')
    data = json.loads(path.read_text())
    app = Path(data['app']).resolve()
    canonical = run / 'smart-cub/apps/prototypes/founder-videos'
    if app == canonical.resolve() or not app.is_relative_to(run.resolve()):
        raise ValueError('Revision must use an isolated task app, not the canonical teaching graph')
    if not app.is_dir() or (app / 'out').is_symlink():
        raise ValueError('Revision app must exist with its own output directory')
    evidence = Path(data.get('reviewEvidenceRoot', ''))
    if not evidence.is_absolute() or evidence.is_symlink() or evidence.resolve() != app / 'out/review-complete':
        raise ValueError('Marketing revision review evidence must stay in its own output directory')
    if not re.fullmatch('[0-9a-f]{64}', data['frozenSourceSnapshot']):
        raise ValueError('Explicit frozen graph fingerprint required')
    if data.get('renderPhases') != ['marketing']:
        raise ValueError('The isolated production revision may render marketing only')
    if data['verifiedNarrationContracts'] != 149:
        raise ValueError('Root must verify all149 narration contracts before activation')
    proof = data['narrationContractProof']
    proof_path = Path(proof['path']).resolve()
    if not proof_path.is_relative_to(run.resolve()) or sha(proof_path) != proof['sha256']:
        raise ValueError('Narration-contract proof changed or escaped the run root')
    contract = json.loads(proof_path.read_text())
    checks = contract.get('checks', [])
    if contract.get('status') != 'pass' or len(checks) != 149 or any(row.get('status') != 'pass' for row in checks):
        raise ValueError('Narration-contract proof must record149 passed checks')
    protected = data['protectedSourceHashes']
    required = {'scripts/review-media.py', 'scripts/review-ledger.py', 'data/scripts.json',
                'data/ctas.json', 'data/cta-assignments.json', 'scripts/render-batch.ts',
                'scripts/render-batch-support.ts'}
    if not required.issubset(protected):
        raise ValueError('Revision handoff lacks protected implementation/context hashes')
    for name, expected in protected.items():
        target = (app / name).resolve()
        if not target.is_relative_to(app) or sha(target) != expected:
            raise ValueError('Protected revision input changed: ' + name)
    validated_helpers(data, run, app)
    return data, sha(path)
