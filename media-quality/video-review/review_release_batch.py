#!/usr/bin/env python3
"""Bounded four-video Flash batches. Complete audio, sampled visuals, saved findings."""
import importlib.util
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import time

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=None
out=r.WORK/'release-reviews'
ledger_path=r.WORK/'budget-ledger.json'
PROMPT='''Review every attached COMPLETE Smart Cub Learning social export separately.
Each supplied file is a 2 fps visual proxy of its exact source export, with the
original encoded audio unchanged. You receive all audio; visual sampling is 1 fps.
State uncertainty about short visual changes; this is not a native text-jitter scan.
Use each REVIEW_SLOT exactly as labelled. Do not stop at the CTA: describe a real
worked example, explanation or factual claim from the body as evidence of inspection.
For kind=cta there is no lesson body: describe the actual standalone offer instead;
never invent a teaching example. The cta_id identifies the reusable ending.
Check teaching accuracy, examples/answers, clarity for ages 9-10 in British English,
narration/captions, mispronunciation, clipped speech, pace, readable text, overlap,
contrast and social UI safe margins. Flag outdated/date-dependent claims for verification.
The supplied user_feedback is prior editorial feedback to verify against this new
export, not a statement that the defect is still present. For new_marketing_voice,
listen for natural, expressive delivery and flag markedly flat, monotonous or forced
delivery. CTA06 should sound calm. In identify-sound-device, listen specifically for
the complete pronunciation of onomatopoeia, including its final unstressed vowel.
Then inspect the complete CTA and join. Speech must finish before a one-second visual
fade; a final black frame is intentional. No yellow left-right block wipes. Normal
gold brand highlights are allowed. Female/non-founder teaching follow CTAs say
Follow for more; James's own voice/portrait can say Follow me. Practice offers must
not be described as diagnostics. A diagnostic CTA uses /free-diagnostic. Teaching
practice CTAs use their relevant subject /11-plus/english/, /maths,
/non-verbal-reasoning or /verbal-reasoning/ path on ONE readable line. Unrelated
tool-specific CTA destinations stay intact. Logos inside genuine website captures
are allowed; a central branded circle needs no second top logo. A founder portrait
is not a second logo. The paper-fold CTA must visibly demonstrate actual folding.
CTA05 should show 2-6 seconds of real product footage after its speech.
Report only actual observed problems with timestamps and evidence. Do not guess
unreadable text from metadata. If you cannot inspect a part, state uncertainty.
Do not invent exercises or incorrect answers, and do not flag deliberate design
or harmless still holds as defects. Do not provide publication approval.
Return ONLY the specified JSON schema, rows in REVIEW_SLOT order.
verdict = no_additional_defects_observed / needs_changes / uncertain.
issues must give seconds within the supplied original duration, issue, evidence,
fix and severity (blocker / minor). Say heard_cta using actual heard words.
first_observed and last_observed describe actual content, not just timestamps.
Where supplied, inspect the native-resolution URL crop from the same exact video
and return observed_url as the actual printed text, or unclear if unreadable.
For follow cards with no website URL return not_applicable.'''


def schema():
    properties={k:{'type':'string'} for k in ['first_observed','last_observed','body_observation','heard_cta','observed_url','verdict']}
    properties.update(slot={'type':'integer'},observed_complete={'type':'boolean'},
        issues={'type':'array','items':{'type':'object','properties':{'seconds':{'type':'number'},
            **{k:{'type':'string'} for k in ['issue','evidence','fix','severity']}},
            'required':['seconds','issue','evidence','fix','severity']}},
        uncertainties={'type':'array','items':{'type':'string'}})
    return {'type':'object','properties':{'reviews':{'type':'array','items':{'type':'object',
        'properties':properties,'required':list(properties)}}},'required':['reviews']}


def answer(response):
    if response['candidates'][0].get('finishReason')!='STOP':raise ValueError('Incomplete model response retained')
    text=''.join(x.get('text','') for x in response['candidates'][0]['content']['parts']).strip()
    # Preserve the full response but accept a valid JSON object after a stray
    # markdown fence or model preamble, without another paid request.
    for match in re.finditer(r'\{',text):
        try:
            value,_=json.JSONDecoder().raw_decode(text[match.start():])
            if isinstance(value,dict) and isinstance(value.get('reviews'),list):return value
        except json.JSONDecodeError:pass
    raise ValueError('No valid review JSON in saved response; do not repeat request')


def proxy(row,proof):
    path=Path(proof['output']);digest=proof['outputSha256']
    if r.sha(path)!=digest:raise ValueError('Committed export changed before review')
    folder=out/'proxies';folder.mkdir(exist_ok=True)
    dest=folder/(digest+'.mp4');receipt=dest.with_suffix('.json')
    if receipt.exists():
        saved=r.read(receipt)
        if saved['sourceSha256']==digest and r.sha(dest)==saved['sha256']:return dest,saved
        raise ValueError('Proxy changed')
    width,height=(720,1280) if row['aspect']=='tall' else (1280,720)
    temp=dest.with_name('.'+dest.stem+'.partial.mp4')
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-y','-i',str(path),
        '-vf',f'fps=2,scale={width}:{height}','-c:v','h264_videotoolbox','-b:v','700k',
        '-c:a','copy','-movflags','+faststart',str(temp)],check=True)
    os.replace(temp,dest)
    def audio_hash(file):
        result=subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(file),'-map','0:a:0',
            '-c:a','copy','-f','hash','-hash','sha256','-'],capture_output=True,text=True,check=True)
        return result.stdout.strip()
    original_audio=audio_hash(path)
    if audio_hash(dest)!=original_audio:raise ValueError('Review proxy did not preserve the complete encoded source audio')
    probe=subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,text=True,check=True)
    duration=float(json.loads(probe.stdout)['format']['duration'])
    saved={'source':str(path),'sourceSha256':digest,'sha256':r.sha(dest),'durationSeconds':duration,
        'visualFramesPerSecond':2,'sourceAndProxyEncodedAudioSha256':original_audio,'audio':'original AAC stream copied, no resynthesis or time stretch'}
    m.atomic_json(receipt,saved);return dest,saved


def main():
    plan=r.read(r.WORK/'repair-plan.json');selected=[x for x in plan['outputs'] if not x['removedFromRelease']]
    client=m.Gemini.__new__(m.Gemini);client._key=(r.OLD/'.runtime-secrets/gemini-key').read_text().strip()
    if client.model()['name']!='models/'+m.MODEL:raise ValueError('Existing Flash model unavailable')
    state={'state':'starting','total':len(selected),'completed':0,'model':m.MODEL,'publicationApproval':False}
    def save(status,**values):
        state.update(state=status,at=m.ledger.now(),pid=os.getpid(),**values);m.atomic_json(out/'status.json',state)
        print(json.dumps(state),flush=True)
    def completed():
        rows={}
        for p in out.glob('batch-*.json'):
            if p.name.endswith('-response.json'):continue
            j=r.read(p)
            if j['state']!='completed_advisory':
                if j.get('reservationId'):raise ValueError('Unresolved paid batch retained; inspect before continuing: '+str(p))
                continue
            if r.sha(Path(j['responsePath']))!=j['responseSha256']:raise ValueError('Review response changed')
            for item,result in zip(j['items'],j['reviews']):
                rows[item['identity']]={'id':item['id'],'kind':item['kind'],'aspect':item['aspect'],
                    'sourceSha256':item['sha256'],'source':item['output'],'advisory':result,'job':str(p),
                    'agentJudgement':'pending'}
        m.atomic_json(out/'findings.json',{'at':m.ledger.now(),'total':len(selected),'completed':len(rows),
            'rows':list(rows.values()),'publicationApproval':False})
        return rows
    try:
        while True:
            done=completed();state['completed']=len(done)
            if len(done)==len(selected):save('all_exact_export_advisories_complete_agent_judgement_pending');return
            assembly=r.WORK/'assembly-progress.json';proofs=r.read(assembly)['outputs'] if assembly.exists() else {}
            batch=[]
            for row in selected:
                identity='|'.join([row['kind'],row['id'],row['aspect']])
                if identity not in done and identity in proofs:
                    batch.append((row,proofs[identity],identity))
                    if len(batch)==4:break
            if not batch:
                save('waiting_for_completed_exports')
                time.sleep(20);continue
            with m.ledger.transaction(ledger_path) as live:
                available=m.ledger.budget_totals(live['budget'])['available_cents']
                review_used=sum(x.get('actual_cents',x['maximum_cents']) for x in live['budget']['reservations'].values() if x['category']=='review')
            if available<5 or review_used>=1200:save('stopped_at_review_budget_limit');return
            parts=[{'text':PROMPT}];files=[];items=[];number=len(list(out.glob('batch-*-response.json')))+1
            job_path=out/f'batch-{number:03d}.json';job={'state':'preparing','model':m.MODEL,'items':items,'at':m.ledger.now(),'samplingFps':1}
            m.atomic_json(job_path,job);save('preparing_four_export_batch',batch=number)
            try:
                for slot,(row,proof,identity) in enumerate(batch):
                    path,proxyproof=proxy(row,proof)
                    item={'identity':identity,'id':row['id'],'aspect':row['aspect'],'kind':row['kind'],'slot':slot,
                          'output':proof['output'],'sha256':proof['outputSha256'],'cta_id':row['ctaId'],'proxy':str(path),
                          'proxySha256':proxyproof['sha256'],'durationSeconds':proxyproof['durationSeconds']}
                    items.append(item);m.atomic_json(job_path,job)
                    def remember(file):
                        files.append(file);job['providerFiles']=files;m.atomic_json(job_path,job)
                    uploaded=client.upload(path,remember)
                    context={k:item[k] for k in ['slot','id','aspect','kind','cta_id','durationSeconds']}
                    context.update(user_feedback=row.get('humanReview',{}).get('notes',''),
                                   new_marketing_voice=row.get('needsNewMarketingNarration',False))
                    parts.extend([{'text':'REVIEW_SLOT '+str(slot)+' '+json.dumps(context)},
                        {'fileData':{'mimeType':'video/mp4','fileUri':uploaded['uri']},'videoMetadata':{'fps':1},'mediaProcessing':'STATIC'}])
                    if row['ctaId'] not in ['cta-sc-01','cta-sc-02','cta-sc-03','cta-sc-04']:
                        crop=out/'proxies'/(proof['outputSha256']+'-native-url.png')
                        cut=proof.get('inputs',{}).get('cutSeconds',0)
                        second=min(item['durationSeconds']-1.2,cut+3)
                        region='900:140:60:1510' if row['aspect']=='tall' else '1680:140:120:885'
                        if not crop.exists():
                            subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-y','-ss',str(max(0,second)),
                                '-i',proof['output'],'-frames:v','1','-vf','crop='+region,str(crop)],check=True)
                        parts.extend([{'text':'Native original-video URL crop for REVIEW_SLOT '+str(slot)},
                            {'inlineData':{'mimeType':'image/png','data':base64.b64encode(crop.read_bytes()).decode()}}])
                        item['nativeUrlCrop']=str(crop);item['nativeUrlCropSha256']=r.sha(crop)
                        m.atomic_json(job_path,job)
                body={'contents':[{'role':'user','parts':parts}],'generationConfig':{'responseMimeType':'application/json',
                    'responseJsonSchema':schema(),'maxOutputTokens':4096,'thinkingConfig':{'thinkingLevel':'low'},
                    'temperature':0,'mediaResolution':'MEDIA_RESOLUTION_MEDIUM'}}
                count,_=client.request('/v1beta/models/'+m.MODEL+':countTokens',{'generateContentRequest':{'model':'models/'+m.MODEL,**body}})
                bound=m.preflight(count['totalTokens']);job['preflight']=bound;m.atomic_json(job_path,job)
                with m.ledger.transaction(ledger_path) as live:
                    current=m.ledger.budget_totals(live['budget'])['available_cents']
                if bound['maximum_cents']>current or review_used+bound['maximum_cents']>1200:
                    job['state']='budget_preflight_rejected';m.atomic_json(job_path,job);save('stopped_at_review_budget_limit');return
                reservation='review:feedback26:export-batch-'+str(number)
                m.ledger.reserve(ledger_path,reservation,bound['maximum_cents'],'review','Four complete-audio sampled-video final export advisories')
                job.update(state='submitted_unknown',reservationId=reservation);m.atomic_json(job_path,job);save('reviewing_four_export_batch',batch=number)
                response,_=client.request('/v1beta/models/'+m.MODEL+':generateContent',body)
                response_path=out/f'batch-{number:03d}-response.json';m.atomic_json(response_path,response)
                job.update(state='response_received',responsePath=str(response_path),responseSha256=r.sha(response_path));m.atomic_json(job_path,job)
                cost=m.usage_cost(response['usageMetadata'])
                if response['usageMetadata']['promptTokenCount']>bound['maximum_input_tokens'] or cost['generated_tokens_including_thoughts']>4096:raise ValueError('Usage exceeds preflight; reservation held')
                m.ledger.settle(ledger_path,reservation,cost['ledger_cents_rounded_up'],'Gemini final export batch '+str(number)+' usage tariff estimate')
                rows=answer(response)['reviews']
                if [x.get('slot') for x in rows]!=list(range(len(items))):raise ValueError('Missing/reordered review slots')
                for item,result in zip(items,rows):
                    if result.get('verdict') not in ['no_additional_defects_observed','needs_changes','uncertain'] or type(result.get('observed_complete')) is not bool:raise ValueError('Invalid verdict/coverage')
                    if not all(isinstance(result.get(k),str) and result[k].strip() for k in ['first_observed','last_observed','body_observation','heard_cta']):raise ValueError('Missing observed body/CTA evidence')
                    for issue in result.get('issues',[]):
                        if type(issue.get('seconds')) not in [int,float] or not 0<=issue['seconds']<=item['durationSeconds']+.6:raise ValueError('Invalid finding timestamp')
                        if issue.get('severity') not in ['blocker','minor']:raise ValueError('Invalid finding severity')
                job.update(state='completed_advisory',reviews=rows,costEstimate=cost);m.atomic_json(job_path,job)
                save('four_export_batch_complete',batch=number)
            finally:
                cleanup=[]
                for file in files:
                    try:client.delete(file['name']);cleanup.append({'name':file['name'],'deleted':True})
                    except Exception as error:cleanup.append({'name':file['name'],'deleted':False,'errorType':type(error).__name__})
                job['providerCleanup']=cleanup;m.atomic_json(job_path,job)
    except Exception as error:
        save('stopped_needs_attention_no_paid_retry',error={'type':type(error).__name__,'message':str(error)[:350]});raise


if __name__=='__main__':
    m=r.helpers();m.ledger.require_studio();out.mkdir(exist_ok=True)
    with m.exclusive(out/'.lock'):main()
