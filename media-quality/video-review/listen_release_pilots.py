#!/usr/bin/env python3
"""One bounded listening advisory of the three initial feedback voice takes."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=r.helpers();m.ledger.require_studio()
out=r.WORK/'pilot-listening';out.mkdir(exist_ok=True)
job_path=out/'job.json';response_path=out/'response.json'
if job_path.exists() and r.read(job_path).get('reservationId'):
    raise RuntimeError('Pilot request already exists; inspect saved response without resubmission')
client=m.Gemini.__new__(m.Gemini);client._key=(r.OLD/'.runtime-secrets/gemini-key').read_text().strip()
names=['marketing-p1-what-121-means','james-cta-sc-06','alice-shared-cta-sc-01']
parts=[{'text':'''Listen to these complete regenerated speech takes separately in exact order.
1. James professional voice clone: a conversational Bucks score explanation, replacing a flat monotone take.
2. James CTA06: calm, friendly free-exercise offer, deliberately avoiding exaggerated excitement.
3. Alice teaching CTA01: must say Follow for more, without me.
Check complete speech, naturalness, expressiveness, audible artifacts, clipped consonants,
spoken performance tags and pronunciation. Report the actual heard opening and ending,
not what the script claims. Return JSON {reviews:[{id,heardOpening,heardEnding,
deliveryVerdict:pass/needs_changes/uncertain,clipping:none/start/end/both/uncertain,
spokenPerformanceTags:boolean,issues:[...]}]} in exact input order. No publication approval.'''}]
for name in names:
    path=r.WORK/'voices'/name/'audio.mp3'
    parts.extend([{'text':name},{'inlineData':{'mimeType':'audio/mp3','data':base64.b64encode(path.read_bytes()).decode()}}])
body={'contents':[{'role':'user','parts':parts}], 'generationConfig':{'responseMimeType':'application/json','temperature':0,'maxOutputTokens':4096,'thinkingConfig':{'thinkingLevel':'low'}}}
count,_=client.request('/v1beta/models/'+m.MODEL+':countTokens',{'generateContentRequest':{'model':'models/'+m.MODEL,**body}})
bound=m.preflight(count['totalTokens']);reservation='review:feedback26:initial-three-voices'
job={'state':'preflight','at':m.ledger.now(),'model':m.MODEL,'sources':names,'preflight':bound}
m.atomic_json(job_path,job);m.ledger.reserve(r.WORK/'budget-ledger.json',reservation,bound['maximum_cents'],'review','Initial three complete regenerated voice takes')
job.update(state='submitted_unknown',reservationId=reservation);m.atomic_json(job_path,job)
response,_=client.request('/v1beta/models/'+m.MODEL+':generateContent',body)
m.atomic_json(response_path,response);job.update(state='response_received',responseSha256=r.sha(response_path));m.atomic_json(job_path,job)
cost=m.usage_cost(response['usageMetadata']);m.ledger.settle(r.WORK/'budget-ledger.json',reservation,cost['ledger_cents_rounded_up'],'Gemini initial voice pilots usage tariff estimate')
spec=importlib.util.spec_from_file_location('remaining',Path(__file__).with_name('review_remaining.py'));remaining=importlib.util.module_from_spec(spec);spec.loader.exec_module(remaining)
text=''.join(x.get('text','') for x in response['candidates'][0]['content']['parts']).strip()
import re
text=re.sub(r'^```(?:json)?\s*','',text);text=re.sub(r'\s*```$','',text)
answer=json.loads(text)
if [x['id'] for x in answer['reviews']]!=names:raise RuntimeError('Pilot response missing/reordered entries')
job.update(state='completed_advisory',reviews=answer['reviews'],costEstimate=cost);m.atomic_json(job_path,job)
print(json.dumps({'state':job['state'],'costCents':cost['ledger_cents_rounded_up'],'reviews':job['reviews']}))
