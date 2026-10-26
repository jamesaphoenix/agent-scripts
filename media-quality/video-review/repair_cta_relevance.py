#!/usr/bin/env python3
"""Align teaching offers and subject destinations using existing CTA exports.

Wait for the current assembly and verifier to finish, and stop review only at
its safe batch boundary. Preserve superseded files and all original feedback.
No new voice/API requests; replacement exports require fresh exact-SHA review.
"""
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

EXPECTED_AFFECTED = 56


def target_cta(row):
    if row.get('removedFromRelease') or row.get('kind') != 'teaching':
        return None
    subject, old = row.get('subject'), row.get('ctaId')
    if old == 'cta-sc-15' and subject in ('english', 'maths', 'nvr', 'vr'):
        return {'english': 'cta-sc-18', 'maths': 'cta-sc-14', 'nvr': 'cta-sc-17', 'vr': 'cta-sc-06'}[subject]
    if old == 'cta-sc-05' and subject in ('english', 'maths', 'vr'):
        return {'english': 'cta-sc-18', 'maths': 'cta-sc-14', 'vr': 'cta-sc-06'}[subject]
    if old == 'cta-sc-07' and subject == 'maths':
        return 'cta-sc-14'
    return None


def live(label):
    result = subprocess.run(['launchctl', 'print', 'gui/501/com.jud.' + label],
                            capture_output=True, text=True)
    match = re.search(r'\n\s*pid = (\d+)', result.stdout)
    if not match:
        return False
    os.kill(int(match.group(1)), 0)
    return True


def main():
    spec = importlib.util.spec_from_file_location('repair', Path(__file__).with_name('release_repair.py'))
    r = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r)
    m = r.helpers()
    m.ledger.require_studio()
    w = r.WORK
    archive = w / 'superseded-cta-relevance'
    archive.mkdir(exist_ok=True)
    status = w / 'cta-relevance-status.json'
    def save(state, **values):
        m.atomic_json(status, {'state': state, 'at': m.ledger.now(), 'pid': os.getpid(), **values})
    def wait():
        if datetime.datetime.now(datetime.timezone.utc) >= datetime.datetime(2026, 10, 27, 7, 30, tzinfo=datetime.timezone.utc):
            raise RuntimeError('Repair deadline reached; inspect and report before handover')
        time.sleep(20)
    installer = Path(__file__).resolve().parents[2] / 'scripts/install-launchd-tasks.sh'
    with m.exclusive(w / '.cta-relevance.lock'):
        try:
            while live('smartcub-release-continuation-20261026') or live('smartcub-release-technical-20261026'):
                save('waiting_for_live_assembly_and_technical_workers')
                wait()
            progress = r.read(w / 'assembly-progress.json')
            journal = archive / 'journal.json'
            if not journal.exists():
                if len(progress['outputs']) != 578:
                    raise ValueError('Original assembly stopped before full scope; do not mutate')
                plan = r.read(w / 'repair-plan.json')
                changes = []
                for row in plan['outputs']:
                    target = target_cta(row)
                    if target:
                        identity = '|'.join(row[k] for k in ('kind', 'id', 'aspect'))
                        cta = w / 'source' / row['subject'] / 'out' / f'{target}-{row["aspect"]}.mp4'
                        receipt = r.read(cta.with_suffix('.render-receipt.json'))
                        if r.sha(cta) != receipt['output']['sha256']:
                            raise ValueError('Replacement CTA receipt mismatch')
                        changes.append({'identity': identity, 'oldCta': row['ctaId'], 'newCta': target,
                                        'proof': progress['outputs'][identity], 'ctaSha256': r.sha(cta),
                                        'replacement': str(cta)})
                if len(changes) != EXPECTED_AFFECTED:
                    raise ValueError('Unexpected scope for the twenty-eight teaching concepts')
                m.atomic_json(journal, {'changes': changes, 'at': m.ledger.now(), 'reason':
                    'Paper-fold offers do not match English, maths or verbal destinations; the four-subject 144-skill offer also overstates a single-subject landing page. Reuse matching female subject offers; preserve James all-subject and James/NVR folding demonstrations.'})
            changes = r.read(journal)['changes']
            stop = w / 'release-reviews/stop-after-batch'
            stop.touch()
            while live('smartcub-final-review-batch-20261026'):
                save('waiting_for_safe_review_batch_boundary')
                wait()
            review_state = r.read(w / 'release-reviews/status.json')['state']
            if review_state not in ('stopped_at_safe_batch_boundary', 'all_exact_export_advisories_complete_agent_judgement_pending'):
                raise ValueError('Reviewer stopped unexpectedly; preserve request state')
            with m.exclusive(w / '.assembly.lock'):
                for name in ('repair-plan.json', 'assembly-progress.json', 'agent-decisions.json'):
                    backup = archive / name
                    if not backup.exists():
                        backup.write_bytes((w / name).read_bytes())
                plan = r.read(w / 'repair-plan.json')
                progress = r.read(w / 'assembly-progress.json')
                decisions = r.read(w / 'agent-decisions.json')
                by_id = {x['identity']: x for x in changes}
                for row in plan['outputs']:
                    identity = '|'.join(row[k] for k in ('kind', 'id', 'aspect'))
                    if identity in by_id:
                        change = by_id[identity]
                        if row['ctaId'] not in (change['oldCta'], change['newCta']):
                            raise ValueError('CTA plan changed outside this repair')
                        row['ctaId'] = row['cta_id'] = change['newCta']
                        row['ctaRelevanceRepair'] = str(journal)
                for change in changes:
                    output = Path(change['proof']['output'])
                    destination = archive / 'videos' / output.relative_to(w / 'videos')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if output.exists():
                        if r.sha(output) != change['proof']['outputSha256']:
                            raise ValueError('Unexpected export bytes; do not archive')
                        if destination.exists():
                            raise ValueError('Both original and archived output exist')
                        os.replace(output, destination)
                    elif not destination.exists() or r.sha(destination) != change['proof']['outputSha256']:
                        raise ValueError('Original export is missing from both locations')
                    old_receipt = output.with_suffix('.receipt.json')
                    if old_receipt.exists():
                        os.replace(old_receipt, destination.with_suffix('.receipt.json'))
                    progress['outputs'].pop(change['identity'], None)
                    decisions['decisions'].pop(change['identity'], None)
                progress.update(completed=len(progress['outputs']), state='pending_cta_relevance_reassembly')
                for name, data in [('repair-plan.json', plan), ('assembly-progress.json', progress), ('agent-decisions.json', decisions)]:
                    m.atomic_json(w / name, data)
                manifest = w / 'videos/manifest.json'
                if manifest.exists():
                    os.replace(manifest, archive / 'superseded-manifest.json')
            save('reassembling_subject_relevant_endings', affected=EXPECTED_AFFECTED)
            subprocess.run(['/opt/homebrew/bin/python3', str(Path(__file__).with_name('assemble_release.py'))], check=True)
            stop.unlink()
            for task in ('smartcub-release-technical-20261026', 'smartcub-final-review-batch-20261026'):
                subprocess.run([str(installer), '--task', task], check=True)
            save('reassembled_pending_fresh_exact_output_review', affected=EXPECTED_AFFECTED, archive=str(archive))
        except Exception as error:
            save('stopped_needs_attention', error=type(error).__name__ + ': ' + str(error)[:350])
            raise


if __name__ == '__main__':
    main()
