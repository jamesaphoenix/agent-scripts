#!/usr/bin/env python3
"""Studio-owned overnight batches, with measured dependencies and hard spending cap."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=r.helpers();m.ledger.require_studio()
script=Path(__file__).parent;status=r.WORK/'night-status.json'
env={k:v for k,v in os.environ.items() if not any(t in k for t in ['KEY','TOKEN','SECRET'])};env['PATH']='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
def save(state,**other):m.atomic_json(status,{'state':state,'at':m.ledger.now(),'pid':os.getpid(),'deadline':'2026-10-27T08:00:00+00:00','budgetCapCents':5000,**other})
def run(name,*args):
    with (r.WORK/(name+'.log')).open('a') as log:
        subprocess.run(list(args),env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
with m.exclusive(r.WORK/'.night-batch.lock'):
    try:
        save('capturing_current_public_pages_and_correcting_counts')
        if not (r.WORK/'fresh-page-captures/capture-evidence.json').exists():
            run('fresh-page-captures','/opt/homebrew/bin/node',str(script/'capture_catalogue.mjs'))
        # Keep all first-pass render sources/media/receipts before fresh assets or facts change.
        for mode in ['james','english','maths','nvr','vr']:
            r.clone(r.WORK/'source'/mode,r.WORK/'archive'/('initial-cta-'+mode))
        run('catalogue-voice-corrections','/opt/homebrew/bin/python3',str(script/'finalize_ctas.py'),'--stage-only')
        while True:
            p=r.WORK/'marketing-body-render-progress.json'
            progress=r.read(p) if p.exists() else {}
            if len(progress.get('outputs',{}))==progress.get('plannedConcepts',-1)*2:break
            job=subprocess.run(['launchctl','print','gui/501/com.jud.smartcub-release-continuation-20261026'],capture_output=True,text=True)
            if not re.search(r'\n\s*pid = \d+',job.stdout):raise ValueError('Marketing renderer stopped before required body outputs were committed')
            save('waiting_for_live_marketing_body_batch',completed=len(progress.get('outputs',{})),expected=progress.get('plannedConcepts',0)*2)
            time.sleep(20)
        save('checking_all_global_yellow_wipe_users')
        run('remaining-global-wipe-bodies','/opt/homebrew/bin/python3',str(script/'render_release_marketing.py'))
        for mode in ['james','english','maths','nvr','vr']:
            root=r.WORK/'source'/mode
            for p in (r.WORK/'fresh-page-captures').glob('*.png'):shutil.copyfile(p,root/'public/cta'/p.name)
        save('rendering_final_cta_facts_and_product_visibility')
        run('cta-fact-finalization','/opt/homebrew/bin/python3',str(script/'finalize_ctas.py'))
        plan=r.read(r.WORK/'repair-plan.json')
        for mode in ['james','english','maths','nvr','vr']:
            ids=sorted({x['ctaId'] for x in plan['outputs'] if not x['removedFromRelease'] and
                (x['kind']=='teaching' and x['subject']==mode if mode!='james' else x['kind'] in ['cta','marketing'])})
            save('batch_rendering_final_reusable_ctas',mode=mode)
            root=r.WORK/'source'/mode
            with (r.WORK/f'cta-final-{mode}.log').open('a') as log:
                r.render_batch(['/opt/homebrew/bin/node','--import','tsx','scripts/render-batch.ts','--ids',','.join(ids),
                    '--aspect','both','--jobs','2','--workers','4','--execute'],cwd=root,env=env,log=log)
        m.atomic_json(r.WORK/'cta-final-pictures-and-facts-ready.json',{'state':'complete_pending_exact_output_review','at':m.ledger.now(),
            'freshPages':str(r.WORK/'fresh-page-captures/capture-evidence.json'),'notPublicationApproval':True})
        save('final_ctas_ready_assembly_and_pronunciation_review_pending')
    except Exception as error:
        save('stopped_needs_attention',error={'type':type(error).__name__,'message':str(error)[:400]})
        raise
