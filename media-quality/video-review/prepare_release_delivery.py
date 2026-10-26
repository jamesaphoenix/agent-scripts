#!/usr/bin/env python3
"""Prepare a checked private delivery generation. Does not switch or publish it."""
import copy
import importlib.util
import json
import os
from pathlib import Path

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def validate(manifest,ledger):
    if ledger.get('pendingRepairs'):
        raise ValueError('Pending targeted repairs must finish before delivery')
    rows=manifest['videos'];checked=ledger['videos']
    target=ledger['scope']['selectedExports']
    if target!=578 or len(rows)!=target or len(checked)!=target:
        raise ValueError('Incomplete selected release scope')
    by_id={x['identity']:x for x in checked}
    identities=['|'.join([x['kind'],x['id'],x['aspect']]) for x in rows]
    if len(by_id)!=target or len(set(identities))!=target or set(identities)!=set(by_id):
        raise ValueError('Duplicate or mismatched delivery identities')
    if not ledger.get('allSelectedReadyForSocial'):
        raise ValueError('Editorial release ledger is incomplete')
    for row,identity in zip(rows,identities):
        check=by_id[identity]
        if not check['readyForSocial'] or check['sourceSha256']!=row['sha256']:
            raise ValueError('Stale or unapproved exact export: '+identity)
        if check.get('distributionRestrictions',[])!=row.get('distribution_restrictions',[]):
            raise ValueError('Distribution restriction mismatch: '+identity)
    budget=ledger['budget']
    if budget['actual_cents']+budget['reserved_cents']>5000:
        raise ValueError('Budget exceeded')
    return by_id


def main():
    m=r.helpers();m.ledger.require_studio();w=r.WORK
    # Rebuild current evidence instead of trusting an old all-ready snapshot.
    import subprocess
    subprocess.run(['/opt/homebrew/bin/python3',str(Path(__file__).with_name('build_release_ledger.py'))],check=True)
    manifest=r.read(w/'videos/manifest.json');ledger=r.read(w/'release-review-ledger.json')
    checked=validate(manifest,ledger)
    if r.read(r.OLD/'human-review/reviews.json')!=r.read(w/'human-feedback-snapshot.json'):
        raise ValueError('Human feedback changed after snapshot; incorporate new feedback first')
    selected=[]
    for row in manifest['videos']:
        source=(w/'videos'/row['path']).resolve()
        if not source.is_relative_to((w/'videos').resolve()) or not source.is_file():
            raise ValueError('Invalid export path')
        if r.sha(source)!=row['sha256']:raise ValueError('Export bytes changed: '+str(source))
        selected.append((row,source))
    output=copy.deepcopy(manifest)
    output.update(contract='smartcub-feedback26-reviewed-private-delivery/v1',
                  approved_for_launch=True,publication_performed=False,
                  approval_basis='Exact-export technical and audiovisual evidence plus recorded Codex editorial decisions; distribution restrictions apply',
                  human_publication_approval=False,at=m.ledger.now())
    for row in output['videos']:
        identity='|'.join([row['kind'],row['id'],row['aspect']]);evidence=checked[identity]
        row['review_state']=evidence['agentDecision']['status']
        row['checks']={'technical':'passed_or_explicitly_resolved','audiovisual':'reviewed',
                       'text_jitter':'waived_by_user','release_ledger':str(w/'release-review-ledger.json')}
    generation='feedback26-'+manifest['generation']
    delivery=w.parents[1]/'delivery/video-delivery'
    target=delivery/'generations'/generation
    if target.exists():
        if r.read(target/'manifest.json').get('generation')!=manifest['generation']:
            raise ValueError('Existing delivery generation conflict')
        for row,_ in selected:
            if r.sha(target/row['path'])!=row['sha256']:raise ValueError('Existing delivery changed')
        m.atomic_json(target/'manifest.json',output)
        m.atomic_json(target/'release-review-ledger.json',ledger)
    else:
        staging=delivery/'generations'/('.preparing-'+generation)
        staging.mkdir(parents=True,exist_ok=True)
        for row,source in selected:
            dest=staging/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                if r.sha(dest)!=row['sha256']:raise ValueError('Partial delivery conflict')
            else:os.link(source,dest)
        m.atomic_json(staging/'manifest.json',output)
        m.atomic_json(staging/'release-review-ledger.json',ledger)
        os.rename(staging,target)
    current=delivery/'current'
    receipt={'at':m.ledger.now(),'generation':generation,'path':str(target),'total':len(selected),
             'previousCurrent':os.readlink(current) if current.is_symlink() else None,
             'currentSwitched':False,'publicationPerformed':False,
             'manifestSha256':r.sha(target/'manifest.json')}
    m.atomic_json(w/'prepared-delivery.json',receipt)
    print(json.dumps(receipt))


if __name__=='__main__':main()
