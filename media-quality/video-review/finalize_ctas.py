#!/usr/bin/env python3
"""Enlarge the actual folding canvas, keeping cropped website UI out of CTA07."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=r.helpers();m.ledger.require_studio()
# Live 26 October catalogue facts: 55 maths skills/35 exercises; 22 NVR skills/19 exercises.
# Keep a dated evidence record and correct both speech and text before final rendering.
facts={'checkedAt':m.ledger.now(),'maths':{'skills':55,'exercises':35,'url':'https://smartcublearning.com/11-plus/maths/'},
       'nvr':{'skills':22,'exercises':19,'url':'https://smartcublearning.com/11-plus/non-verbal-reasoning/'},
       'english':{'skills':41,'exercises':28},'vr':{'skills':26,'exercises':21},
       'evidence':'Live public pages read in headless Chromium on Studio, with titles and visible counts checked.'}
m.atomic_json(r.WORK/'live-catalogue-counts-20261026.json',facts)
jobs=[]
for mode,ids in [('james',['cta-sc-12','cta-sc-14','cta-sc-16','cta-sc-17']),('maths',['cta-sc-14']),('nvr',['cta-sc-17'])]:
    root=r.WORK/'source'/mode;rows=r.read(root/'data/ctas.json')
    for row in rows:
        if row['id'] not in ids:continue
        row['script']=row['script'].replace('33 interactive','35 interactive').replace('18 free interactive','19 free interactive')
        row['sub']=row['sub'].replace('33 interactive','35 interactive').replace('18 free interactive','19 free interactive')
        if row['id']=='cta-sc-12':
            row.update(head='**Free exercises** to set',script='[warmly] Need practice to set between lessons? Explore free 11+ exercises, each with its own link to share, at Smart Cub Learning.com forward slash eleven plus.')
        if row['id']=='cta-sc-16':
            row.update(sub='Copy the code. Paste it. Share the practice.',script='[warmly] Got your own website? You can embed free 11+ exercises on your page. Copy the code at Smart Cub Learning.com forward slash eleven plus.')
        jobs.append({'identity':'catalogue-facts-v2-'+mode+'-'+row['id'],'id':row['id'],'root':str(root),
            'script':row['script'],'voice':r.JAMES if mode=='james' else r.ALICE,
            'model':'eleven_v4' if mode=='james' else 'eleven_multilingual_v2','mode':mode})
    m.atomic_json(root/'data/ctas.json',rows)
r.voices(m,jobs,0)
if '--stage-only' in sys.argv:raise SystemExit(0)
video=r.WORK/'product-capture/release-paper-canvas.mp4'
if not video.exists():
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-y','-i',str(r.WORK/'product-capture/release-product-footage.mp4'),
        '-vf','crop=440:440:320:210','-c:v','h264_videotoolbox','-b:v','8M',str(video)],check=True)
for mode in ['james','maths','nvr']:
    root=r.WORK/'source'/mode
    p=root/'src/kit/CtaLibrary.tsx';text=p.read_text()
    if 'release-paper-canvas.mp4' not in text:
        text=text.replace("const is3d = cta.media === 'lesson3d'", "const is3d = cta.media === 'lesson3d' || cta.id === 'cta-sc-07'")
        text=text.replace('height: (w * 346) / 680 + 0', "height: cta.id === 'cta-sc-07' ? w * 0.62 : (w * 346) / 680")
        text=text.replace('durationInFrames={280}', "durationInFrames={cta.id === 'cta-sc-07' ? 150 : 280}")
        text=text.replace("src={staticFile('cta/lesson3d.mp4')}", "src={staticFile(cta.id === 'cta-sc-07' ? 'cta/release-paper-canvas.mp4' : 'cta/lesson3d.mp4')}")
        text=text.replace("{Headline(108, 880, 'center')}", "{Headline(cta.id === 'cta-sc-07' ? 88 : 108, 880, 'center')}")
        p.write_text(text)
    shutil.copyfile(video,root/'public/cta/release-paper-canvas.mp4')
    env={k:v for k,v in os.environ.items() if not any(s in k for s in ['TOKEN','KEY','SECRET'])};env['PATH']='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    with (r.WORK/f'cta07-final-{mode}.log').open('a') as log:
        r.render_batch(['/opt/homebrew/bin/node','--import','tsx','scripts/render-batch.ts','--ids',','.join(['cta-sc-07']+(['cta-sc-12','cta-sc-14','cta-sc-16','cta-sc-17'] if mode=='james' else ['cta-sc-14'] if mode=='maths' else ['cta-sc-17'])),
            '--aspect','both','--jobs','2','--workers','4','--execute'],cwd=root,env=env,log=log)
m.atomic_json(r.WORK/'cta07-final-visibility.json',{'state':'rendered_enlarged_real_paper_canvas_pending_review',
    'at':m.ledger.now(),'modes':['james','maths','nvr'],'source':str(video),'sha256':r.sha(video)})
