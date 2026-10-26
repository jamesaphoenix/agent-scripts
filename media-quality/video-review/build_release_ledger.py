#!/usr/bin/env python3
"""Combine frozen feedback and exact-export evidence; never infer editorial approval."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
spec=importlib.util.spec_from_file_location('reviews',Path(__file__).with_name('review_release_batch.py'))
reviews=importlib.util.module_from_spec(spec);spec.loader.exec_module(reviews)


def ready(proof,technical,review,decision):
    digest=proof.get('outputSha256')
    if not digest or technical.get('sha256')!=digest or review.get('sourceSha256')!=digest:return False
    if decision.get('sourceSha256')!=digest or decision.get('status') not in ['ready','ready_with_distribution_limits']:return False
    if not decision.get('reason') or not decision.get('evidence'):return False
    advisory=review.get('advisory',{})
    if not advisory.get('observed_complete') and not decision.get('fullCoverageEvidence'):return False
    for findings,resolved in [(technical.get('issues',[]),'resolvedTechnicalIssues'),
                              (advisory.get('issues',[]),'resolvedAdvisoryIssues'),
                              (advisory.get('uncertainties',[]),'resolvedUncertainties')]:
        dispositions=decision.get(resolved,{})
        if any(not dispositions.get(str(i)) for i in range(len(findings))):return False
    return True


def main():
    m=r.helpers();m.ledger.require_studio();w=r.WORK;plan=r.read(w/'repair-plan.json')
    p=w/'assembly-progress.json';proofs=r.read(p)['outputs'] if p.exists() else {}
    advisory=reviews.collect_current_reviews(w/'release-reviews',proofs)
    technical={}
    for p in (w/'technical-verification').glob('*.result.json'):
        value=r.read(p)
        if proofs.get(value['identity'],{}).get('outputSha256')==value['sha256']:technical[value['identity']]=value
    p=w/'agent-decisions.json';decisions=r.read(p).get('decisions',{}) if p.exists() else {}
    rows=[];removed=[]
    for row in plan['outputs']:
        identity='|'.join([row['kind'],row['id'],row['aspect']])
        if row['removedFromRelease']:
            removed.append({'identity':identity,'reason':'User requested removal','previousFeedback':row['humanReview'],'originalPreserved':row['input']});continue
        proof=proofs.get(identity,{});check=technical.get(identity,{});review=advisory.get(identity,{})
        decision=decisions.get(identity,{})
        rows.append({'identity':identity,'id':row['id'],'kind':row['kind'],'aspect':row['aspect'],
            'path':row['path'],'sourceSha256':proof.get('outputSha256'),'previousSha256':row['sha256'],
            'previousFeedback':row['humanReview'],'requirements':row['requirements'],
            'ctaId':row['ctaId'],'ctaVoiceGroup':row['subject'] if row['kind']=='teaching' else 'james',
            'distributionRestrictions':row.get('distribution_restrictions',[]),
            'renderEvidence':proof,'technicalEvidence':check,'audiovisualReviewEvidence':review,
            'agentDecision':decision,'readyForSocial':ready(proof,check,review,decision)})
    budget=r.read(w/'budget-ledger.json')['budget'];totals=m.ledger.budget_totals(budget)
    ledger={'contract':'smartcub-feedback26-release-review-ledger/v1','at':m.ledger.now(),
        'scope':{'originalExports':plan['inputCount'],'selectedExports':plan['selectedForRelease'],'removedExports':len(removed)},
        'counts':{'assembled':len(proofs),'technicalChecks':len(technical),'audiovisualAdvisories':len(advisory),
                  'readyForSocial':sum(x['readyForSocial'] for x in rows)},
        'allSelectedReadyForSocial':len(rows)==plan['selectedForRelease'] and all(x['readyForSocial'] for x in rows),
        'budget':totals,'publicationPerformed':False,'textJitterScan':'waived_by_user',
        'removed':removed,'videos':rows}
    m.atomic_json(w/'release-review-ledger.json',ledger)
    print(__import__('json').dumps({k:ledger[k] for k in ['scope','counts','allSelectedReadyForSocial','budget']}))

if __name__=='__main__':main()
