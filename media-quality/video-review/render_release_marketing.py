#!/usr/bin/env python3
"""Render only marketing bodies changed by narration/layout/wipe feedback."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=r.helpers();m.ledger.require_studio()
plan=r.read(r.WORK/'repair-plan.json')
rows=[x for x in plan['outputs'] if x['kind']=='marketing' and not x['removedFromRelease']]
by={}
scripts={x['id']:x for x in r.read(r.WORK/'source/james/data/scripts.json')}
for row in rows:
    note=row['humanReview'].get('notes','').lower()
    if row['needsNewMarketingNarration'] or scripts.get(row['id'],{}).get('style')=='bold' or 'yellow transition' in note or row['id']=='t21-term-report':
        by.setdefault(row['sourceRoot'],set()).add(row['id'])
progress_path=r.WORK/'marketing-body-render-progress.json'
progress=r.read(progress_path) if progress_path.exists() else {'roots':{},'outputs':{},'plannedConcepts':len({i for ids in by.values() for i in ids})}
progress['plannedConcepts']=len({i for ids in by.values() for i in ids})
m.atomic_json(progress_path,progress)
with m.exclusive(r.WORK/'.marketing-body-render.lock'):
    for original,ids in sorted(by.items()):
        ids={identity for identity in ids if not all(identity+'|'+aspect in progress['outputs'] for aspect in ['tall','wide'])}
        if not ids:continue
        target=r.WORK/'source'/('marketing-'+__import__('hashlib').sha256(original.encode()).hexdigest()[:14])
        r.clone(Path(original),target)
        # Reuse the exact revised James CTA kit, audio and footage while retaining
        # this root's bespoke body components and previously corrected layouts.
        for rel in ['src/kit/CtaLibrary.tsx','src/kit/Film.tsx','src/kit/StandaloneCta.tsx','data/ctas.json']:
            shutil.copyfile(r.WORK/'source/james'/rel,target/rel)
        for identity in ['cta-sc-05','cta-sc-06','cta-sc-10']:
            for rel in [f'public/audio/{identity}.mp3',f'data/words/{identity}.json']:
                shutil.copyfile(r.WORK/'source/james'/rel,target/rel)
        shutil.copyfile(r.WORK/'product-capture/release-product-footage.mp4',target/'public/cta/release-product-footage.mp4')
        avatars=r.read(target/'data/avatars.json');avatars['cta-sc-05']=False;m.atomic_json(target/'data/avatars.json',avatars)
        assignments=r.read(target/'data/cta-assignments.json')
        for identity in ids:
            if identity in r.DIAGNOSTIC:assignments[identity]='cta-sc-10'
            if identity in r.REVOICE:
                voice=r.WORK/'voices'/('marketing-'+identity)
                if not (voice/'job.json').exists() or r.read(voice/'job.json')['state']!='complete':
                    raise ValueError('Required new voice not ready: '+identity)
                shutil.copyfile(voice/'audio.mp3',target/'public/audio'/f'{identity}.mp3')
                shutil.copyfile(voice/'words.json',target/'data/words'/f'{identity}.json')
        m.atomic_json(target/'data/cta-assignments.json',assignments)
        bold=target/'src/kit/custom/look-bold-kit.tsx'
        if bold.exists():
            text=bold.read_text();text=re.sub(r'\{w > 0 && w < 1 \? <div style=\{\{.*?background: YEL, borderRadius: 24 \}\} /> : null\}', '{/* Removed yellow wipe. */}',text)
            if 'w > 0 && w < 1 ?' in text:raise ValueError('Unpatched yellow wipe')
            bold.write_text(text)
        if 't21-term-report' in ids:
            p=target/'src/kit/custom/t21-term-report.tsx';text=p.read_text()
            text=text.replace("<div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>","<div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 100px', alignItems: 'center', gap: 12 }}>")
            text=text.replace("width: pick(230, 220), fontWeight: 800, fontSize: pick(40, 46)","gridColumn: '1 / -1', minWidth: 0, fontWeight: 800, fontSize: pick(40, 46)")
            text=text.replace("position: 'relative', flex: 1, height: 40", "position: 'relative', minWidth: 0, height: 40")
            p.write_text(text)
        progress['roots'][original]={'state':'rendering','target':str(target),'ids':sorted(ids),'at':m.ledger.now()}
        m.atomic_json(progress_path,progress)
        env={k:v for k,v in os.environ.items() if not any(t in k for t in ['KEY','SECRET','TOKEN'])}
        env['PATH']='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        log=r.WORK/('marketing-'+target.name+'.log')
        with log.open('a') as f:
            subprocess.run(['/opt/homebrew/bin/node','--import','tsx','scripts/render-batch.ts','--ids',','.join(sorted(ids)),
                '--aspect','both','--jobs','2','--workers','4','--execute'],cwd=target,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
        for identity in ids:
            for aspect in ['tall','wide']:
                path=target/'out'/f'{identity}-{aspect}.mp4';receipt=path.with_suffix('.render-receipt.json')
                if r.sha(path)!=r.read(receipt)['output']['sha256']:raise ValueError('Render receipt mismatch')
                progress['outputs'][identity+'|'+aspect]={'output':str(path),'sha256':r.sha(path),'receipt':str(receipt),'state':'rendered_pending_review'}
        progress['roots'][original].update(state='rendered',at=m.ledger.now());m.atomic_json(progress_path,progress)
        print(json.dumps({'state':'body_root_rendered','outputs':len(progress['outputs']),'root':str(target)}),flush=True)
