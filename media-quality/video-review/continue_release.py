#!/usr/bin/env python3
"""Studio-owned continuation after the reusable CTA renderer completes."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=r.helpers();m.ledger.require_studio()
status_path=r.WORK/'continuation-status.json'
env={k:v for k,v in os.environ.items() if not any(t in k for t in ['KEY','SECRET','TOKEN'])}
env['PATH']='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
def save(state,**values):
    m.atomic_json(status_path,{'state':state,'at':m.ledger.now(),'pid':os.getpid(),**values})
with m.exclusive(r.WORK/'.continuation.lock'):
    try:
        while True:
            p=r.WORK/'render-status.json'
            if p.exists() and r.read(p)['state']=='reusable_ctas_rendered_pending_visual_review':break
            job=subprocess.run(['launchctl','print','gui/501/com.jud.smartcub-feedback-release-20261026'],capture_output=True,text=True)
            # A stale status file is not proof that work is still running.
            live=re.search(r'\n\s*pid = (\d+)',job.stdout)
            if not live:raise ValueError('CTA renderer no longer has a live service process; inspect failure before continuation')
            pid=int(live.group(1));os.kill(pid,0)
            save('waiting_for_live_cta_renderer',rendererPid=pid)
            time.sleep(20)
        save('finalising_paper_fold_cta_visibility')
        subprocess.run(['/opt/homebrew/bin/python3',str(Path(__file__).with_name('finalize_ctas.py'))],env=env,check=True)
        save('rendering_changed_marketing_bodies')
        subprocess.run(['/opt/homebrew/bin/python3',str(Path(__file__).with_name('render_release_marketing.py'))],env=env,check=True)
        save('assembling_available_release_candidates')
        subprocess.run(['/opt/homebrew/bin/python3',str(Path(__file__).with_name('assemble_release.py'))],env=env,check=True)
        progress=r.read(r.WORK/'assembly-progress.json')
        save('assembled_pending_body_repairs_and_exact_output_review',assembled=len(progress['outputs']),
             target=progress['planned'],bodyRepairs=progress.get('blockedBodyRepairs',[]),approvedForRelease=False)
    except Exception as error:
        save('stopped_needs_attention',error={'type':type(error).__name__,'message':str(error)[:350]})
        raise
