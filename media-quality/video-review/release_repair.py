#!/usr/bin/env python3
"""Isolated Smart Cub feedback revision, capped at US$50 in a new ledger."""
import argparse
import base64
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

BASE = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
OLD = BASE / 'workflow/cta-11plus-routing-20261021'
WORK = BASE / 'workflow/feedback-release-20261026'
HELPER = OLD / 'source/james/scripts/review-media.py'
JAMES = '1CdujsFVRQeoIxTwAYmB'
ALICE = 'Xb7hH8MSUJpSbSDYk0k2'
REMOVED = {'t14-im-stupid', 't36-parent-sits-in', 't62-stress-signs-checklist', 't63-message-boundaries'}
REVOICE = {'p1-what-121-means', 'p2-selection-review', 'p3-when-to-start', 'p4-bucks-test-in-60s',
           'p5-collect-words', 'p6-weekly-routine', 'p7-same-mistake', 'p8-answer-sheet',
           'p51-long-multiplication-do-dont', 't1-diagnose-first', 't2-stop-marking', 't3-group-of-four',
           't4-show-progress', 't5-verbal-is-half', 't6-homework-that-gets-done', 't7-past-papers', 't8-methods-not-tricks'}
DIAGNOSTIC = {'p17-should-we-do-mocks', 'p20-ten-minute-habit', 'p37-youngest-in-the-year',
              'p41-free-gl-familiarisation', 'p61-platform-checklist',
              'teaching:english.grammar.complete-finite-clause', 'teaching:english.grammar.match-subject-verb',
              'teaching:english.grammar.order-sentence-elements', 'teaching:english.punctuation.use-capital-letters',
              'teaching:english.reading.distinguish-fact-opinion'}
SUBJECT = {'english': ('English', 'english/'), 'maths': ('maths', 'maths'),
           'nvr': ('non-verbal reasoning', 'non-verbal-reasoning'),
           'vr': ('verbal reasoning', 'verbal-reasoning/')}


def read(p):
    return json.loads(p.read_text())


def pending_source_aspects(aspects_by_id, outputs, target):
    pending = {identity: {aspect for aspect in aspects if
        Path(outputs.get(identity+'|'+aspect, {}).get('output', '/missing')).parent.parent != target}
        for identity, aspects in aspects_by_id.items()}
    return {identity: aspects for identity, aspects in pending.items() if aspects}


def render_batch(command, *, cwd, env, log):
    """Wait for other Studio renderers, retrying only an explicit lease collision."""
    lease = Path(tempfile.gettempdir()) / 'smart-cub-founder-render.lock'
    while True:
        while lease.exists():
            # Never steal another job's lease, including a lease needing inspection.
            time.sleep(15)
        log.flush()
        offset = log.tell()
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log,
                                stderr=subprocess.STDOUT)
        log.flush()
        if result.returncode == 0:
            return
        with Path(log.name).open() as evidence:
            evidence.seek(offset)
            failure = evidence.read()
        if 'Another render job holds ' not in failure:
            raise subprocess.CalledProcessError(result.returncode, command)
        time.sleep(15)


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def helpers():
    spec = importlib.util.spec_from_file_location('prior_media', HELPER)
    media = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media)
    return media


def key(row):
    return '|'.join(row[x] for x in ['kind', 'id', 'aspect', 'sha256'])


def cta_rows(original, subject=None):
    rows = json.loads(json.dumps(original))
    for row in rows:
        ident = row['id']
        if subject and row['kind'] == 'follow':
            row['script'] = row['script'].replace('Follow me for more', 'Follow for more')
            row['head'] = row['head'].replace('Follow me for more', 'Follow for more')
        if ident == 'cta-sc-05':
            row.update(circle=False, media='exercises', head='Practise **11+ skills for free**',
                sub='Explore real exercises, including paper folding.',
                script='[warmly] Practise 11+ techniques for free at Smart Cub Learning.com forward slash eleven plus. Try paper folding and more.',
                destination='https://smartcublearning.com/11-plus', url='smartcublearning.com/11-plus')
        elif ident == 'cta-sc-06':
            row.update(script='[warmly] Explore free 11+ exercises at Smart Cub Learning.com forward slash eleven plus. Pick an exercise and give it a go.',
                       head='**Free 11+ exercises**', sub='Pick an exercise and give it a go.')
        elif ident == 'cta-sc-10':
            row.update(head='Try the **free 11+ diagnostic**', sub='Find a useful starting point for practice.',
                script='[warmly] Find a useful starting point with our free 11+ diagnostic at Smart Cub Learning.com forward slash free diagnostic.',
                url='smartcublearning.com/free-diagnostic', destination='https://smartcublearning.com/free-diagnostic', media='diagnostic')
        if subject and row.get('destination') == 'https://smartcublearning.com/11-plus':
            label, suffix = SUBJECT[subject]
            row['url'] = 'smartcublearning.com/11-plus/' + suffix
            row['destination'] = 'https://' + row['url']
            row['script'] = row['script'].replace('forward slash eleven plus', 'forward slash eleven plus forward slash ' + label)
        if subject:
            row['script'] = re.sub(r'\[[^]]+\]', '', row['script']).strip()
    return rows


def prepare(media):
    WORK.mkdir(exist_ok=True)
    plan_path = WORK / 'repair-plan.json'
    if plan_path.exists():
        return read(plan_path)
    manifest = read(OLD / 'rendered-videos/manifest.json')
    human = read(OLD / 'human-review/reviews.json')
    marketing = read(OLD / 'marketing-render-progress.json')['outputs']
    baseline = read(BASE / 'delivery/video-delivery/generations/22902139c0816b6f0b1e2d3e/manifest.json')
    previous = {(r['kind'], r['id'], r['aspect']): r for r in baseline['videos']}
    outputs = []
    for row in manifest['videos']:
        decision = human['reviews'].get(key(row), {})
        if row['kind'] == 'marketing':
            proof = marketing.get(row['id'] + '|' + row['aspect'])
            source_root = str(Path(proof['output']).parent.parent) if proof else str(Path(previous[(row['kind'],row['id'],row['aspect'])]['source_project_path']).parent.parent)
        else:
            source_root = None
        outputs.append({**row, 'input': str(OLD / 'rendered-videos' / row['path']),
            'humanReview': decision, 'removedFromRelease': row['id'] in REMOVED,
            'sourceRoot': source_root, 'needsNewMarketingNarration': row['id'] in REVOICE,
            'ctaId': 'cta-sc-10' if row['id'] in DIAGNOSTIC else row['cta_id'],
            'subject': row['id'].split(':',1)[1].split('.')[0] if row['kind']=='teaching' else None,
            'requirements': ['One-second visual fade after all speech; no clipped narration.',
                'No yellow block wipe transitions.', 'Keep approved body corrections and social safe margins.']})
    plan = {'contract': 'smartcub-feedback-release-plan/v1', 'at': media.ledger.now(),
        'sourceManifest': str(OLD/'rendered-videos/manifest.json'), 'sourceManifestSha256': sha(OLD/'rendered-videos/manifest.json'),
        'sourceHumanReviewsSha256': sha(OLD/'human-review/reviews.json'), 'inputCount': len(outputs),
        'selectedForRelease': sum(not r['removedFromRelease'] for r in outputs),
        'removedConcepts': sorted(REMOVED), 'newVoiceConcepts': sorted(REVOICE),
        'additionalBudgetCents': 5000, 'voiceTargetCents': 3500, 'reviewTargetCents': 1200, 'contingencyCents': 300,
        'textJitterScanEnabled': False, 'publicationAuthorised': False, 'outputs': outputs}
    media.atomic_json(WORK / 'human-feedback-snapshot.json', human)
    media.atomic_json(plan_path, plan)
    ledger = {'schema_version': 1, 'objective': 'Additional US$50 feedback repair revision; original ledger and holds remain untouched.',
              'created_at': media.ledger.now(), 'budget': {'cap_cents': 5000, 'currency': 'USD', 'reservations': {}}}
    media.atomic_json(WORK / 'budget-ledger.json', ledger)
    return plan


def clone(source, target):
    if not target.exists():
        target.parent.mkdir(exist_ok=True, parents=True)
        subprocess.run(['cp','-cR',str(source),str(target)],check=True)
        dep = target / 'node_modules'
        if dep.is_symlink():
            dep.unlink()
            dep.symlink_to((source/'node_modules').resolve(strict=True),target_is_directory=True)
    # Some reviewed source snapshots intentionally use read-only files. Only
    # make our isolated copy editable; never follow package/asset symlinks.
    if target.is_symlink() or not target.resolve().is_relative_to(WORK.resolve()):
        raise ValueError('Clone target must remain within this isolated revision')
    for current, dirs, files in os.walk(target, followlinks=False):
        dirs[:]=[name for name in dirs if name not in ['node_modules','.git','out'] and not (Path(current)/name).is_symlink()]
        folder=Path(current)
        os.chmod(folder,folder.stat().st_mode|0o700)
        for name in files:
            p=folder/name
            if not p.is_symlink():os.chmod(p,p.stat().st_mode|0o200)
    return target


def patch_kit(root):
    marker = root / '.feedback26-kit-patched'
    if marker.exists():return
    path = root / 'src/kit/CtaLibrary.tsx'
    text = path.read_text()
    text = text.replace('  const mediaStarts = ctaMediaSequenceStarts(timing, frame, useCurrentFrame())', '  const localFrame = useCurrentFrame()\n  const mediaStarts = ctaMediaSequenceStarts(timing, frame, localFrame)')
    a = text.index('    const h = size * 2\n', text.index('  const Url ='))
    b = text.index('\n  const FollowButton', a)
    replacement = '''    // Long subject URLs remain one line inside the full safe-width lower band.
    const width = tall ? 900 : 1680
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')!
    let fs = tall ? 42 : 48
    ctx.font = `800 ${fs}px Inter, sans-serif`
    while (ctx.measureText(cta.url).width > width - 72 && fs > 20) {
      fs -= 1
      ctx.font = `800 ${fs}px Inter, sans-serif`
    }
    return <div style={{ height: 92 }} />
  }
  const FullWidthUrl = () => {
    if (follow) return null
    const width = tall ? 900 : 1680
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')!
    let fs = tall ? 42 : 48
    ctx.font = `800 ${fs}px Inter, sans-serif`
    while (ctx.measureText(cta.url).width > width - 72 && fs > 20) { fs--; ctx.font = `800 ${fs}px Inter, sans-serif` }
    return <div style={{ position: 'absolute', left: tall ? 60 : 120, width, top: tall ? 1540 : 914,
      height: 88, borderRadius: 44, background: SC.teal, color: 'white', display: 'flex', alignItems: 'center',
      justifyContent: 'center', whiteSpace: 'nowrap', fontSize: fs, fontWeight: 800,
      boxShadow: '0 10px 24px rgba(7,85,101,.15)' }}>{cta.url}</div>
  }
'''
    if 'const FullWidthUrl' not in text:
        text = text[:a] + replacement + text[b:]
        text = text.replace('{body}</div>', '{body}</div>\n      <FullWidthUrl />')
    # Practice footage is a complete product demonstration after CTA05 speech.
    if 'release-product-footage.mp4' not in text:
        close = "      <FullWidthUrl />"
        tail = '''
      {cta.id === 'cta-sc-05' && frame >= timing.voiceFrame + Math.ceil(cta.words.durationMs / 1000 * fps) ? (
        <Sequence from={Math.max(0, localFrame + timing.voiceFrame + Math.ceil(cta.words.durationMs / 1000 * fps) - frame)} layout="none">
          <AbsoluteFill style={{ background: SC.cream, alignItems: 'center', justifyContent: 'center' }}>
            <OffthreadVideo src={staticFile('cta/release-product-footage.mp4')} muted
              style={{ width: '92%', height: '86%', objectFit: 'contain' }} />
          </AbsoluteFill>
        </Sequence>
      ) : null}'''
        text=text.replace(close,close+tail)
    # Paper fold CTA shows actual moving product footage in the existing media frame.
    text=text.replace("<Img src={staticFile(`cta/${cta.media}-mob.png`)}", "{cta.id === 'cta-sc-07' ? <Sequence from={mediaStarts.lesson3d} layout={'none'}><Loop durationInFrames={150} layout={'none'}><OffthreadVideo src={staticFile('cta/release-paper-fold-closeup.mp4')} muted style={{ width: '100%', height: '100%', objectFit: 'contain' }} /></Loop></Sequence> : <Img src={staticFile(`cta/${cta.media}-mob.png`)}")
    text=text.replace("width: inner }} />", "width: inner }} />}" ) if "{cta.id === 'cta-sc-07' ?" in text else text
    text=text.replace("<Img src={staticFile(`cta/${cta.media}-desk.png`)}", "{cta.id === 'cta-sc-07' ? <Sequence from={mediaStarts.lesson3d} layout={'none'}><Loop durationInFrames={150} layout={'none'}><OffthreadVideo src={staticFile('cta/release-paper-fold-closeup.mp4')} muted style={{ width: '100%', height: '100%', objectFit: 'contain' }} /></Loop></Sequence> : <Img src={staticFile(`cta/${cta.media}-desk.png`)}")
    text=text.replace("width: zw }} />", "width: zw }} />}" ) if "{cta.id === 'cta-sc-07' ?" in text else text
    text = text.replace('      <FullWidthUrl />', '''      <FullWidthUrl />''')
    # This topmost layer also covers embedded CTAs in marketing films.
    text = text.replace('    </AbsoluteFill>\n  )\n}', '''      <AbsoluteFill style={{ background: '#000', pointerEvents: 'none', opacity: Math.max(0, Math.min(1, (frame - (timing.endFrame - 31)) / 30)) }} />
    </AbsoluteFill>
  )
}''')
    path.write_text(text)
    # Fade only the picture, wholly after audio. Standalone timing and embedded
    # marketing timing share the same extra hold for CTA05's product preview.
    p = root / 'src/kit/StandaloneCta.tsx'
    t = p.read_text().replace('cta.words.durationMs + 1700', "cta.words.durationMs + (cta.id === 'cta-sc-05' ? 5700 : 1700)")
    t = t.replace('<SmartCubCta cta={cta} timing={timing} />', '<SmartCubCta cta={cta} timing={timing} />')
    if False: # The shared Card supplies one consistent fade layer.
        t = t.replace('      </AbsoluteFill>', '''        <AbsoluteFill data-testid="release-end-fade" style={{ background: '#000', pointerEvents: 'none',
          opacity: Math.max(0, Math.min(1, (frame - (timing.endFrame - 31)) / 30)) }} />
      </AbsoluteFill>''')
    p.write_text(t)
    p=root/'src/kit/Film.tsx';t=p.read_text()
    t=t.replace('a.cta.words.durationMs + CTA_HOLD_MS','a.cta.words.durationMs + CTA_HOLD_MS + (a.cta.id === \'cta-sc-05\' ? 4000 : 0)')
    p.write_text(t)
    p=root/'src/kit/custom/look-bold-kit.tsx'
    if p.exists():
        t=p.read_text();t=re.sub(r'\{w > 0 && w < 1 \? <div style=\{\{.*?background: YEL, borderRadius: 24 \}\} /> : null\}', '{/* Yellow scene wipe removed by user feedback. */}',t)
        if 'w > 0 && w < 1 ?' in t:raise ValueError('Yellow wipe patch failed')
        p.write_text(t)
    marker.write_text('shared CTA fixes, full-width single-line URLs, silent picture-only ending fade\n')


def stage(media, plan):
    original=read(OLD/'source/james/data/ctas.json')
    scripts={r['id']:r for r in read(OLD/'source/james/data/scripts.json')}
    jobs=[]
    subjects={r['subject'] for r in plan['outputs'] if r['kind']=='teaching'}
    for mode in ['james']+sorted(subjects):
        root=clone(OLD/'source'/('james' if mode=='james' else 'alice'),WORK/'source'/mode)
        patch_kit(root)
        rows=cta_rows(original,None if mode=='james' else mode)
        media.atomic_json(root/'data/ctas.json',rows)
        assigned={r['ctaId'] for r in plan['outputs'] if not r['removedFromRelease'] and
                  (r['kind']=='teaching' and r['subject']==mode if mode!='james' else r['kind'] in ['cta','marketing'])}
        selected = assigned if mode!='james' else {'cta-sc-05','cta-sc-06','cta-sc-10'}
        avatars=read(root/'data/avatars.json');avatars['cta-sc-05']=False
        media.atomic_json(root/'data/avatars.json',avatars)
        for row in rows:
            if row['id'] not in selected:continue
            # Follow and diagnostic takes can share exact audio across subjects.
            family=mode if mode=='james' or row['kind']!='follow' and row['id']!='cta-sc-10' else 'alice-shared'
            jobs.append({'identity':family+'-'+row['id'],'id':row['id'],'script':row['script'],
                'root':str(root),'voice':JAMES if mode=='james' else ALICE,
                'model':'eleven_v4' if mode=='james' else 'eleven_multilingual_v2',
                'registry':'data/ctas.json','mode':mode})
    for identity in sorted(REVOICE):
        row=dict(scripts[identity]);text=re.sub(r'\[[^]]+\]', '',row['script']).strip()
        sentences=re.split(r'(?<=[.!?])\s+',text)
        tagged='[conversational] '+ ' '.join(('[thoughtful] ' if i==len(sentences)//2 else '[encouraging] ' if i==len(sentences)-2 else '')+s for i,s in enumerate(sentences))
        jobs.append({'identity':'marketing-'+identity,'id':identity,'script':tagged,'root':None,
            'voice':JAMES,'model':'eleven_v4','registry':'data/scripts.json','mode':'marketing'})
    media.atomic_json(WORK/'voice-plan.json',{'jobs':jobs,'uniqueRequests':len({j['identity'] for j in jobs}),
        'characterCount':sum(len(j['script']) for j in {j['identity']:j for j in jobs}.values()),
        'budgetLedger':str(WORK/'budget-ledger.json')})
    return jobs


ONES=['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen']
TENS=['','','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety']
def number(n):
    if n<20:return ONES[n]
    if n<100:return TENS[n//10]+('-'+ONES[n%10] if n%10 else '')
    if n<1000:return ONES[n//100]+' hundred'+(' and '+number(n%100) if n%100 else '')
    if 1900<=n<2100:return number(n//100)+' '+(number(n%100) if n%100 else 'hundred')
    raise ValueError('Unhandled number '+str(n))


def spoken_parts(script, tags, aliases=None):
    aliases=aliases or {}
    text='';display=[];spans=[]
    for part in re.split(r'(\[[^]]+\])',script):
        if re.fullmatch(r'\[[^]]+\]',part):
            if tags:text+=(' ' if text else '')+part
            continue
        for word in part.split():
            m=re.fullmatch(r'([^A-Za-z0-9]*)(.*?)([^A-Za-z0-9+%]*)',word);pre,core,post=m.groups()
            said='eleven plus' if core=='11+' else 'G L' if core=='GL' else number(int(core[:-1]))+' percent' if re.fullmatch(r'\d+%',core) else number(int(core)) if core.isdigit() else core
            said=pre+aliases.get(core.lower(),said)+post
            text+=(' ' if text else '')
            start=len(text);text+=said;spans.append((start,len(text)));display.append(word)
    return text,display,spans


def secret_once(name):
    path=WORK/'.runtime-secrets'/name
    if path.exists():
        if path.stat().st_mode&0o077:raise ValueError('Unsafe credential permissions')
        return path.read_text().strip()
    path.parent.mkdir(mode=0o700,exist_ok=True)
    env=os.environ.copy()
    if not env.get('OP_SERVICE_ACCOUNT_TOKEN'):
        p=subprocess.run(['/usr/bin/security','find-generic-password','-s','op_service_account_token_cli_automation','-w'],capture_output=True,text=True,timeout=15)
        if p.returncode:raise ValueError('Cached service token unavailable')
        env['OP_SERVICE_ACCOUNT_TOKEN']=p.stdout.strip()
    p=subprocess.run(['/opt/homebrew/bin/op','read','op://api-keys/ElevenLabs/api_key'],env=env,capture_output=True,text=True,timeout=45)
    if p.returncode:raise ValueError('One-time ElevenLabs credential read failed; no retry')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(p.stdout.strip()+'\n')
    return p.stdout.strip()


def voices(media,jobs,limit):
    keyvalue=secret_once('elevenlabs-key');unique={j['identity']:j for j in jobs}
    ledger_path=WORK/'budget-ledger.json';done=0
    priority = {'marketing-p1-what-121-means': 0, 'james-cta-sc-06': 1, 'alice-shared-cta-sc-01': 2}
    for identity,job in sorted(unique.items(), key=lambda pair: priority.get(pair[0], 10)):
        directory=WORK/'voices'/identity;directory.mkdir(parents=True,exist_ok=True)
        status_path=directory/'job.json';response_path=directory/'response.json'
        if status_path.exists() and read(status_path).get('state')=='complete':
            old=read(status_path)
            if old['scriptSha256']!=hashlib.sha256(job['script'].encode()).hexdigest() or old['voice']!=job['voice'] or old['model']!=job['model']:
                raise ValueError('Completed voice identity reused with changed inputs: '+identity)
            if sha(directory/'audio.mp3')!=old['audioSha256'] or sha(directory/'words.json')!=old['wordsSha256']:
                raise ValueError('Completed voice outputs changed: '+identity)
            continue
        text,display,spans=spoken_parts(job['script'],job['model']=='eleven_v4',job.get('pronunciationAliases'))
        settings=job.get('voiceSettings') or {'stability':.35 if job['mode']=='marketing' else .5,'similarity_boost':.8,'use_speaker_boost':True}
        if status_path.exists() and read(status_path).get('reservationId'):
            if not response_path.exists():raise ValueError('Uncertain previous request: '+identity)
            status=read(status_path)
        else:
            # Reserve a conservative $0.60 per 1,000 characters, above the published
            # $0.08 rate. Keep this full hold until exact account usage is reconciled.
            bound=max(15,math.ceil(len(text)*.06))
            with media.ledger.transaction(ledger_path) as live:
                totals=media.ledger.budget_totals(live['budget'])
                voice_liability=sum(x.get('actual_cents',x['maximum_cents']) for x in live['budget']['reservations'].values() if x['category']=='voice')
            if bound>totals['available_cents'] or voice_liability+bound>3500:raise ValueError('Voice allocation limit reached')
            reservation='voice:feedback26:'+hashlib.sha256(identity.encode()).hexdigest()[:28]
            media.ledger.reserve(ledger_path,reservation,bound,'voice','ElevenLabs '+identity+'; full conservative hold until account billing reconciliation')
            status={'identity':identity,'state':'submitted_unknown','at':media.ledger.now(),'reservationId':reservation,
                'maximumCents':bound,'voice':job['voice'],'model':job['model'],'settings':settings,'inputCharacters':len(text),
                'scriptSha256':hashlib.sha256(job['script'].encode()).hexdigest()}
            media.atomic_json(status_path,status)
            req=urllib.request.Request('https://api.elevenlabs.io/v1/text-to-speech/'+job['voice']+'/with-timestamps?output_format=mp3_44100_192',
                data=json.dumps({'text':text,'model_id':job['model'],'voice_settings':settings}).encode(),
                headers={'xi-api-key':keyvalue,'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=180) as response:
                status['requestId']=response.headers.get('request-id') or response.headers.get('x-request-id')
                status['characterCost']=response.headers.get('character-cost')
                media.atomic_json(status_path,status)
                body=json.load(response)
            media.atomic_json(response_path,body)
            status.update(state='response_received',responseSha256=sha(response_path))
            media.atomic_json(status_path,status)
        body=read(response_path);alignment=body['alignment'];actual=''.join(alignment['characters'])
        if actual!=text:raise ValueError('Alignment differs from exact request text: '+identity)
        audio=directory/'audio.mp3';audio.write_bytes(base64.b64decode(body['audio_base64']))
        result=subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(audio)],capture_output=True,text=True,check=True)
        duration=round(float(result.stdout)*1000);words=[]
        for i,(token,(start,end)) in enumerate(zip(display,spans)):
            if not text[start:end].startswith('<phoneme'):
                while start<end-1 and not text[start].isalnum():start+=1
                while end>start+1 and not text[end-1].isalnum():end-=1
            words.append({'i':i,'t':token,'s':round(alignment['character_start_times_seconds'][start]*1000),
                          'e':round(alignment['character_end_times_seconds'][end-1]*1000)})
        media.atomic_json(directory/'words.json',{'id':job['id'],'voice':job['voice'],'model':job['model'],'preset':'feedback26',
            'spoken':text,'durationMs':duration,'words':words})
        status.update(state='complete',at=media.ledger.now(),audioSha256=sha(audio),wordsSha256=sha(directory/'words.json'),
                      costState='conservative_hold_retained_until_billing_reconciled')
        media.atomic_json(status_path,status);done+=1
        print(json.dumps({'voice':identity,'state':'complete','durationMs':duration,'maximumCents':status['maximumCents']}),flush=True)
        if limit and done>=limit:break
    # Install by exact unique-take identity into the isolated roots only.
    for job in jobs:
        directory=WORK/'voices'/job['identity']
        if not (directory/'job.json').exists() or read(directory/'job.json')['state']!='complete' or not job['root']:continue
        root=Path(job['root']);shutil.copyfile(directory/'audio.mp3',root/'public/audio'/f"{job['id']}.mp3")
        shutil.copyfile(directory/'words.json',root/'data/words'/f"{job['id']}.json")


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','stage','voices','build-ctas'])
    parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args()
    if re.sub('[^a-z0-9]','',socket.gethostname().split('.')[0].lower())!='jamessmacstudio':raise ValueError('Mac Studio only')
    media=helpers();plan=prepare(media)
    if args.command=='prepare':print(json.dumps({'outputs':plan['selectedForRelease'],'removed':8,'plan':str(WORK/'repair-plan.json')}))
    elif args.command=='stage':jobs=stage(media,plan);print(json.dumps({'voiceJobs':len(jobs),'uniqueTakes':len({j['identity'] for j in jobs})}))
    else:
        jobs=read(WORK/'voice-plan.json')['jobs']
        voices(media,jobs,args.limit)
        if args.command=='build-ctas':
            for mode in ['james','english','maths','nvr','vr']:
                root=WORK/'source'/mode
                ids=sorted({r['ctaId'] for r in plan['outputs'] if not r['removedFromRelease'] and
                    (r['kind']=='teaching' and r['subject']==mode if mode!='james' else r['kind'] in ['cta','marketing'])})
                status={'at':media.ledger.now(),'state':'rendering_reusable_ctas','mode':mode,'ids':ids,'paidCallsInRender':0}
                media.atomic_json(WORK/'render-status.json',status)
                env={k:v for k,v in os.environ.items() if not any(t in k for t in ['KEY','TOKEN','SECRET'])}
                env['PATH']='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
                with (WORK/f'cta-render-{mode}.log').open('a') as log:
                    subprocess.run(['/opt/homebrew/bin/node','--import','tsx','scripts/render-batch.ts',
                        '--ids',','.join(ids),'--aspect','both','--jobs','2','--workers','4','--execute'],
                        cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            media.atomic_json(WORK/'render-status.json',{'at':media.ledger.now(),'state':'reusable_ctas_rendered_pending_visual_review'})
