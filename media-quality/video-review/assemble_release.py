#!/usr/bin/env python3
"""Join selected body and revised CTA, retaining receipt evidence for each export."""
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import time

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=r.helpers()


def probe(path):
    p=subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,text=True,check=True)
    return float(json.loads(p.stdout)['format']['duration'])


def join(row, output, cta, body, cut, media):
    receipt=output.with_suffix('.receipt.json')
    inputs={'body':str(body),'bodySha256':r.sha(body),'cutSeconds':cut,'cta':str(cta),'ctaSha256':r.sha(cta),
            'id':row['id'],'aspect':row['aspect'],'ctaId':row['ctaId']}
    if receipt.exists():
        old=r.read(receipt)
        if old.get('inputs')==inputs and output.exists() and r.sha(output)==old['outputSha256']:return old
        raise ValueError('Existing committed assembled output differs; preserve and inspect: '+str(output))
    # New standalone CTAs have 0.4 seconds before speech. Trim to a 60 ms lead,
    # keeping the complete first consonant. Each body cut is measured from its
    # own prior receipt or aligned final word rather than a guessed duration.
    scan=subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','info','-i',str(cta),'-t','2',
        '-vn','-af','silencedetect=noise=-45dB:d=0.015','-f','null','-'],capture_output=True,text=True,check=True)
    beginning=re.search(r'silence_start: 0(?:\.0+)?[^\n]*\n.*?silence_end: ([0-9.]+)',scan.stderr,re.S)
    cta_trim=max(0,min(.8,float(beginning.group(1))-.04)) if beginning else 0
    if os.statvfs(r.WORK).f_bavail*os.statvfs(r.WORK).f_frsize<50*1024**3:raise ValueError('Storage below floor')
    temp=output.with_name('.'+output.stem+'.rendering.mp4');temp.parent.mkdir(exist_ok=True,parents=True)
    filters=f'[0:v]trim=end={cut:.6f},setpts=PTS-STARTPTS[v0];[0:a]atrim=end={cut:.6f},asetpts=PTS-STARTPTS[a0];[1:v]trim=start={cta_trim:.6f},setpts=PTS-STARTPTS[v1];[1:a]atrim=start={cta_trim:.6f},asetpts=PTS-STARTPTS[a1];[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]'
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-y','-i',str(body),'-i',str(cta),'-filter_complex',filters,
        '-map','[v]','-map','[a]','-c:v','h264_videotoolbox','-b:v','10M','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','48000',
        '-movflags','+faststart',str(temp)],check=True)
    expected=cut+probe(cta)-cta_trim;actual=probe(temp)
    if abs(actual-expected)>.15:raise ValueError('Assembled duration mismatch')
    os.replace(temp,output)
    record={'contract':'smartcub-feedback26-assembled-output/v1','at':media.ledger.now(),'inputs':inputs,
            'output':str(output),'outputSha256':r.sha(output),'durationSeconds':actual,'ctaTrimSeconds':cta_trim,
            'visualReview':'pending','audioJoinCheck':'pending','publicationApproval':False}
    media.atomic_json(receipt,record)
    return record


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--kinds',nargs='+',default=['teaching','marketing','cta']);args=parser.parse_args()
    m.ledger.require_studio();plan=r.read(r.WORK/'repair-plan.json')
    while not (r.WORK/'cta-final-pictures-and-facts-ready.json').exists():
        p=subprocess.run(['launchctl','print','gui/501/com.jud.smartcub-night-batch-20261026'],capture_output=True,text=True)
        if not re.search(r'\n\s*pid = \d+',p.stdout):raise ValueError('Final CTA correction worker is not live; do not assemble stale CTA assets')
        time.sleep(20)
    marketing_path=r.WORK/'marketing-body-render-progress.json';marketing=r.read(marketing_path)['outputs'] if marketing_path.exists() else {}
    progress_path=r.WORK/'assembly-progress.json';progress=r.read(progress_path) if progress_path.exists() else {'outputs':{},'planned':plan['selectedForRelease']}
    with m.exclusive(r.WORK/'.assembly.lock'):
        while True:
            progress['blockedBodyRepairs']=[]
            for row in plan['outputs']:
                if row['removedFromRelease'] or row['kind'] not in args.kinds:continue
                output=r.WORK/'videos'/row['path'];identity=row['kind']+'|'+row['id']+'|'+row['aspect']
                if identity in progress['outputs']:
                    old=progress['outputs'][identity]
                    if r.sha(Path(old['output']))!=old['outputSha256']:raise ValueError('Completed export changed')
                    continue
                mode=row['subject'] if row['kind']=='teaching' else 'james'
                cta=r.WORK/'source'/mode/'out'/f"{row['ctaId']}-{row['aspect']}.mp4"
                if not cta.with_suffix('.render-receipt.json').exists():raise ValueError('Revised CTA missing receipt')
                if r.sha(cta)!=r.read(cta.with_suffix('.render-receipt.json'))['output']['sha256']:raise ValueError('CTA receipt mismatch')
                if row['kind']=='cta':
                    output.parent.mkdir(parents=True,exist_ok=True)
                    if not output.exists():os.link(cta,output)
                    result={'output':str(output),'outputSha256':r.sha(output),'visualReview':'pending','publicationApproval':False}
                elif row['kind']=='teaching':
                    name=row['id'].split(':',1)[1]
                    # These explicit body repairs must be completed before their export.
                    if name in ['english.language.identify-sound-device','english.punctuation.mark-possession']:
                        replacement=r.WORK/'teaching-body-repairs'/f'{name}-{row["aspect"]}.json'
                        if not replacement.exists():
                            progress.setdefault('blockedBodyRepairs',[])
                            if identity not in progress['blockedBodyRepairs']:progress['blockedBodyRepairs'].append(identity)
                            m.atomic_json(progress_path,progress);continue
                        bodyproof=r.read(replacement);body=Path(bodyproof['output']);cut=bodyproof['cutSeconds']
                    else:
                        prior=r.read(r.OLD/'teaching-successor'/f'{name}-{row["aspect"]}-cta.receipt.json')
                        body=Path(prior['source']);cut=prior['cutSeconds']
                    result=join(row,output,cta,body,cut,m)
                else:
                    bodyproof=marketing.get(row['id']+'|'+row['aspect'])
                    if bodyproof:
                        body=Path(bodyproof['output']);root=body.parent.parent
                    else:
                        body=Path(row['input']);root=Path(row['sourceRoot'])
                    # Changed narrations retain their new aligned word boundaries.
                    words=r.read(root/'data/words'/f"{row['id']}.json")
                    last=words['words'][-1]['e']/1000
                    cut=min(words['durationMs']/1000,last+.06)
                    result=join(row,output,cta,body,cut,m)
                progress['outputs'][identity]=result;progress.update(at=m.ledger.now(),completed=len(progress['outputs']))
                m.atomic_json(progress_path,progress)
                print(json.dumps({'assembled':progress['completed'],'planned':progress['planned'],'id':row['id'],'aspect':row['aspect']}),flush=True)
            if len(progress['outputs'])==progress['planned']:
                progress.update(state='all_578_assembled_pending_exact_output_review',at=m.ledger.now(),blockedBodyRepairs=[])
                m.atomic_json(progress_path,progress)
                catalogue=[]
                for chosen in plan['outputs']:
                    if chosen['removedFromRelease']:continue
                    ident='|'.join([chosen['kind'],chosen['id'],chosen['aspect']])
                    proof=progress['outputs'][ident]
                    mode=chosen['subject'] if chosen['kind']=='teaching' else 'james'
                    ctas={x['id']:x for x in r.read(r.WORK/'source'/mode/'data/ctas.json')}
                    cta=ctas[chosen['ctaId']]
                    metadata={k:chosen[k] for k in ['id','kind','aspect','title','audience','distribution_restrictions'] if k in chosen}
                    catalogue.append({**metadata,'path':chosen['path'],'sha256':proof['outputSha256'],
                        'cta_id':chosen['ctaId'],'cta_destination':cta.get('destination','https://'+cta['url']),
                        'source_project_path':proof['output'],'checks':{},'review_state':'pending_fresh_exact_output_judgement',
                        'previous_human_review':chosen['humanReview']})
                import hashlib
                generation=hashlib.sha256(json.dumps([(x['path'],x['sha256']) for x in catalogue]).encode()).hexdigest()[:24]
                m.atomic_json(r.WORK/'videos/manifest.json',{'contract':'smartcub-feedback26-release-candidates/v1',
                    'at':m.ledger.now(),'generation':generation,'total':len(catalogue),'approved_for_launch':False,
                    'publication_performed':False,'text_jitter':'waived_by_user','videos':catalogue})
                break
            worker=subprocess.run(['launchctl','print','gui/501/com.jud.smartcub-teaching-body-repairs-20261026'],capture_output=True,text=True)
            if not re.search(r'\n\s*pid = \d+',worker.stdout):
                raise ValueError('Explicit teaching body repairs still missing and their worker is not live')
            time.sleep(20)



if __name__=='__main__':main()
