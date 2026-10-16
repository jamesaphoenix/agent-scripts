"""Validate the explicit task-owned marketing revision handoff, without provider calls."""
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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
    if data['canonicalEvidenceRoot'] != str(canonical / 'out/review-complete'):
        raise ValueError('Revision must preserve the canonical absolute review cache')
    if not re.fullmatch('[0-9a-f]{64}', data['frozenSourceSnapshot']):
        raise ValueError('Explicit frozen graph fingerprint required')
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
    return data, sha(path)
