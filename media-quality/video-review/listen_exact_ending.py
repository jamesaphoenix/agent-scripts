#!/usr/bin/env python3
"""One bounded audio-only check when a batch report misattributes an ending."""
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import subprocess

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('identity');parser.add_argument('--check-brand',action='store_true');args=parser.parse_args()
    m=r.helpers();m.ledger.require_studio();proof=r.read(r.WORK/'assembly-progress.json')['outputs'][args.identity]
    if r.sha(Path(proof['output']))!=proof['outputSha256']:raise ValueError('Final export changed')
    folder=r.WORK/'targeted-ending-listening'/proof['outputSha256']
    if args.check_brand:folder=folder/'brand-check'
    folder.mkdir(parents=True,exist_ok=True)
    job_path=folder/'job.json';response_path=folder/'response.json'
    if job_path.exists() and r.read(job_path).get('reservationId'):
        raise ValueError('Existing paid request retained; inspect response without resubmission')
    audio=folder/'ending.mp3';cut=proof.get('inputs',{}).get('cutSeconds',0)
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-y','-ss',str(max(0,cut-.15)),
                    '-i',proof['output'],'-vn','-c:a','libmp3lame','-b:a','128k',str(audio)],check=True)
    prompt='''Listen to this single complete audio excerpt. Transcribe every audible word,
including the website address, and describe the offer being made. Report clipped
speech or unclear words. Do not infer words from a supposed script or other clips.
Return JSON with transcript:string, offer:string, clipping:string, uncertainties:string[].'''
    if args.check_brand:
        prompt+='''
Specifically distinguish the audible words Smart Cub Learning from Smart Club Learning.
Does the speaker actually pronounce an L in Cub, or is Club merely a likely spelling
normalisation? Listen to the sound, not the plausible business name. Return additional
brandHeard:string (Cub/Club/unclear) and phoneticEvidence:string. If ambiguous, say so
in uncertainties. The prior transcription is disputed; do not assume either answer.'''
    body={'contents':[{'role':'user','parts':[{'text':prompt},{'inlineData':{'mimeType':'audio/mp3','data':base64.b64encode(audio.read_bytes()).decode()}}]}],
          'generationConfig':{'responseMimeType':'application/json','temperature':0,'maxOutputTokens':4096,'thinkingConfig':{'thinkingLevel':'low'}}}
    client=m.Gemini.__new__(m.Gemini);client._key=(r.OLD/'.runtime-secrets/gemini-key').read_text().strip()
    count,_=client.request('/v1beta/models/'+m.MODEL+':countTokens',{'generateContentRequest':{'model':'models/'+m.MODEL,**body}})
    bound=m.preflight(count['totalTokens']);ledger=r.WORK/'budget-ledger.json'
    with m.ledger.transaction(ledger) as live:
        used=sum(x.get('actual_cents',x['maximum_cents']) for x in live['budget']['reservations'].values() if x['category']=='review')
    if used+bound['maximum_cents']>1200:raise ValueError('Review allocation would be exceeded')
    reservation='review:feedback26:exact-ending-'+proof['outputSha256']+('-brand' if args.check_brand else '')
    job={'state':'preflight','identity':args.identity,'source':proof['output'],'sourceSha256':proof['outputSha256'],
         'audio':str(audio),'audioSha256':r.sha(audio),'excerptStartSeconds':max(0,cut-.15),'preflight':bound,'at':m.ledger.now()}
    m.atomic_json(job_path,job);m.ledger.reserve(ledger,reservation,bound['maximum_cents'],'review','Single exact-export ending attribution check')
    job.update(state='submitted_unknown',reservationId=reservation);m.atomic_json(job_path,job)
    response,_=client.request('/v1beta/models/'+m.MODEL+':generateContent',body)
    m.atomic_json(response_path,response);job.update(state='response_received',responsePath=str(response_path),responseSha256=r.sha(response_path));m.atomic_json(job_path,job)
    cost=m.usage_cost(response['usageMetadata'])
    if response['usageMetadata']['promptTokenCount']>bound['maximum_input_tokens'] or cost['generated_tokens_including_thoughts']>4096:raise ValueError('Usage exceeds reserved preflight')
    m.ledger.settle(ledger,reservation,cost['ledger_cents_rounded_up'],'Exact ending attribution usage tariff estimate')
    if response['candidates'][0].get('finishReason')!='STOP':raise ValueError('Incomplete response retained')
    text=''.join(x.get('text','') for x in response['candidates'][0]['content']['parts'])
    result=json.loads(text)
    if not all(isinstance(result.get(k),str) and result[k] for k in ['transcript','offer','clipping']) or not isinstance(result.get('uncertainties'),list):raise ValueError('Invalid response retained')
    if args.check_brand and (result.get('brandHeard') not in ['Cub','Club','unclear'] or not result.get('phoneticEvidence')):
        raise ValueError('Brand distinction missing; response retained without retry')
    job.update(state='completed_advisory',result=result,costEstimate=cost);m.atomic_json(job_path,job)
    print(json.dumps({'path':str(job_path),'result':result,'costCents':cost['ledger_cents_rounded_up']}))


if __name__=='__main__':main()
