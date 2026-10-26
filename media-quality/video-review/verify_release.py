#!/usr/bin/env python3
"""Exact-file integrity, ending fade/silence and audio-join checks. No jitter scan."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
FF='/opt/homebrew/bin/ffmpeg'

def silence_spans(text,duration):
    spans=[];start=None
    for kind,value in re.findall(r'silence_(start|end): ([0-9.]+)',text):
        if kind=='start':start=float(value)
        elif start is not None:spans.append((start,float(value)));start=None
    if start is not None:spans.append((start,duration))
    return spans

def inspect(identity,proof):
    path=Path(proof['output']);digest=r.sha(path)
    if digest!=proof['outputSha256']:raise ValueError('Committed export changed: '+str(path))
    p=subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,check=True)
    data=json.loads(p.stdout);duration=float(data['format']['duration']);streams=data['streams']
    video=next(x for x in streams if x['codec_type']=='video');audio=next(x for x in streams if x['codec_type']=='audio')
    aspect=identity.rsplit('|',1)[1];expected=(1080,1920) if aspect=='tall' else (1920,1080)
    issues=[]
    if (video['width'],video['height'])!=expected:issues.append('Unexpected export dimensions')
    if audio.get('sample_rate')!='48000':issues.append('Unexpected audio sample rate')
    # Decode only the last 1.5 seconds, at four samples per second, for the required
    # ending fade. This deliberately does not analyse text position or jitter.
    frames=subprocess.run([FF,'-nostdin','-v','error','-sseof','-1.5','-i',str(path),'-an',
        '-vf','fps=4,scale=16:16,format=gray','-f','rawvideo','-'],capture_output=True,check=True).stdout
    means=[round(sum(frames[i:i+256])/256,3) for i in range(0,len(frames),256) if len(frames[i:i+256])==256]
    # Container duration can extend beyond video due to AAC padding. Decode a
    # longer tail and take its actual last picture, not a possibly empty 100ms.
    final=subprocess.run([FF,'-nostdin','-v','error','-sseof','-1.5','-i',str(path),'-an',
        '-vf','scale=16:16,format=gray','-f','rawvideo','-'],capture_output=True,check=True).stdout
    final_mean=round(sum(final[-256:])/256,3) if len(final)>=256 else None
    if final_mean is None or final_mean>8:issues.append('Final decoded picture not sufficiently faded to black')
    if len(means)>=4 and not means[-1]<means[-4]:issues.append('No decreasing end fade found')
    tail=min(4,duration)
    silent=subprocess.run([FF,'-nostdin','-v','info','-sseof',str(-tail),'-i',str(path),'-vn',
        '-af','silencedetect=noise=-45dB:d=0.02','-f','null','-'],capture_output=True,text=True,check=True)
    spans=silence_spans(silent.stderr,tail)
    ending=next((tail-a for a,b in reversed(spans) if b>=tail-.06),0)
    if ending<.95:issues.append('Speech or sound detected within required final one-second fade')
    cut=proof.get('inputs',{}).get('cutSeconds');join=None
    if cut is not None:
        start=max(0,cut-.8);length=1.6
        scan=subprocess.run([FF,'-nostdin','-v','info','-ss',str(start),'-i',str(path),'-t',str(length),'-vn',
            '-af','silencedetect=noise=-45dB:d=0.02','-f','null','-'],capture_output=True,text=True,check=True)
        offset=cut-start
        gap=next(((a,b) for a,b in silence_spans(scan.stderr,length) if a<=offset<=b),None)
        join={'cutSeconds':cut,'silenceAcrossJoinSeconds':round(gap[1]-gap[0],4) if gap else 0}
        if gap and gap[1]-gap[0]>.25:issues.append('Audio silence across body/CTA join exceeds 250 ms')
    return {'identity':identity,'source':str(path),'sha256':digest,'durationSeconds':duration,
        'dimensions':[video['width'],video['height']],'endFrameMeanLuminance':means,'lastFrameMeanLuminance':final_mean,
        'endingSilenceSeconds':round(ending,4),'join':join,'issues':issues,
        'status':'passed' if not issues else 'needs_inspection','jitterScan':'disabled',
        'editorialApproval':False}

def main():
    m=r.helpers();m.ledger.require_studio();folder=r.WORK/'technical-verification';folder.mkdir(exist_ok=True)
    target=r.read(r.WORK/'repair-plan.json')['selectedForRelease'];done={}
    for p in folder.glob('*.result.json'):
        result=r.read(p);done[result['identity']]=result
    with m.exclusive(folder/'.lock'),concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            p=r.WORK/'assembly-progress.json';proofs=r.read(p)['outputs'] if p.exists() else {}
            tasks={pool.submit(inspect,k,v):(k,v) for k,v in proofs.items()
                   if done.get(k,{}).get('sha256')!=v['outputSha256']
                   or done.get(k,{}).get('lastFrameMeanLuminance') is None}
            for future in concurrent.futures.as_completed(tasks):
                result=future.result();key=result['identity'];m.atomic_json(folder/(result['sha256']+'.result.json'),result);done[key]=result
                m.atomic_json(folder/'status.json',{'completed':len(done),'target':target,
                    'needsInspection':sum(bool(x['issues']) for x in done.values()),'at':m.ledger.now(),'pid':os.getpid()})
            if len(done)==target:
                m.atomic_json(folder/'report.json',{'total':target,'rows':list(done.values()),'jitterScan':'disabled','editorialApproval':False});return
            time.sleep(20)

if __name__=='__main__':main()
