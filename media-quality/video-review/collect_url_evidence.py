#!/usr/bin/env python3
"""Local OCR for disputed URL crops only. Evidence, never automatic approval."""
import importlib.util
import json
from pathlib import Path
import subprocess

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def main():
    m=r.helpers();m.ledger.require_studio();w=r.WORK;folder=w/'release-reviews'
    plan={f"{x['kind']}|{x['id']}|{x['aspect']}":x for x in r.read(w/'repair-plan.json')['outputs']}
    target=w/'targeted-url-ocr.json'
    saved=r.read(target) if target.exists() else {'contract':'smartcub-disputed-url-evidence/v1','rows':{}}
    current=r.read(w/'assembly-progress.json')['outputs'];pending=[]
    for p in sorted(folder.glob('batch-*.json')):
        if p.name.endswith('-response.json'):continue
        job=r.read(p)
        if job['state']!='completed_advisory':continue
        for item,review in zip(job['items'],job['reviews']):
            if current.get(item['identity'],{}).get('outputSha256')!=item['sha256']:continue
            if not item.get('nativeUrlCrop') or not review['issues']:continue
            if not any('url' in (x['issue']+' '+x['evidence']).lower() or 'domain' in (x['issue']+' '+x['evidence']).lower() for x in review['issues']):continue
            crop=Path(item['nativeUrlCrop']);digest=r.sha(crop)
            if saved['rows'].get(item['sha256'],{}).get('cropSha256')==digest:continue
            row=plan[item['identity']];mode=row['subject'] if row['kind']=='teaching' else 'james'
            cta=next(x for x in r.read(w/'source'/mode/'data/ctas.json') if x['id']==row['ctaId'])
            pending.append((item,digest,cta['url']))
    if pending:
        proc=subprocess.run([str(w/'read-url-crops')],input=json.dumps([i['nativeUrlCrop'] for i,_,_ in pending]),
                            capture_output=True,text=True,check=True)
        rows=json.loads(proc.stdout)
        if len(rows)!=len(pending):raise ValueError('Incomplete OCR response')
        for (item,digest,expected),ocr in zip(pending,rows):
            if ocr['path']!=item['nativeUrlCrop']:raise ValueError('OCR crop mismatch')
            lines=ocr.get('lines',[])
            match=len(lines)==1 and lines[0]['text']==expected and lines[0]['confidence']>=.95
            saved['rows'][item['sha256']]={**ocr,'identity':item['identity'],'sourceSha256':item['sha256'],
                'cropSha256':digest,'expectedUrl':expected,'exactSingleLineMatch':match,
                'editorialApproval':False,'at':m.ledger.now()}
            print(json.dumps({'identity':item['identity'],'expected':expected,'observed':[x['text'] for x in lines],'match':match}))
    saved['at']=m.ledger.now();m.atomic_json(target,saved)
    print(json.dumps({'totalDisputedCrops':len(saved['rows']),'newCrops':len(pending),
                      'nonMatching':[x['identity'] for x in saved['rows'].values() if not x['exactSingleLineMatch']]}))


if __name__=='__main__':main()
