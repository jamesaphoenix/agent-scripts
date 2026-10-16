#!/usr/bin/env python3
"""Pinned two-output scale source successor, no paid work or quality approval."""
import datetime,fcntl,hashlib,json,os,re,socket,subprocess,sys,time
from pathlib import Path
RUN=Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
APP=RUN/'revision/smart-cub/apps/prototypes/founder-videos'
RUNNER=RUN/'teaching-repairs/runners/scale-0b03f8210'
STATUS=RUN/'workflow/teaching-scale-dispatch-status.json'
PRIOR_JOBS=[('argument-layout', ['v3-y6-organise-argument-wide', 'v3-y6-organise-argument-tall'], 'ecd4f410a022033ac0989950beaae09336387eab0ae4c575320d1d34d18e02a0', 'argument-style-lab')]
JOBS=[('scale-layout', ['v3-m-scale-by-power-of-ten-wide', 'v3-m-scale-by-power-of-ten-tall'], 'c8586d066ef7a8275bb1723e4118fde337693a3c3b5fc0096f2a9261217c9d99')]
NODE='/opt/homebrew/opt/node@22/bin/node'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def atomic(p,d):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2)+'\n')
 with t.open('rb')as f:os.fsync(f.fileno())
 os.replace(t,p)
def save(d):atomic(STATUS,d)
def alive(pid):
 try:os.kill(pid,0);return True
 except ProcessLookupError:return False
def snapshots_verify(rows):
 for row in rows:
  project=Path(row['project']);snapshot=row['snapshot']
  actual=set()
  for folder in ['src','data','public']:
   assert (project/folder).is_dir(),'Source folder missing'
   for target in (project/folder).rglob('*'):
    assert not target.is_symlink(),'Source symlink unsupported'
    if target.is_file():actual.add(str(target.relative_to(project)))
  actual.update(['package.json','package-lock.json','tsconfig.json'])
  assert actual=={f['path']for f in snapshot['files']},'Source file inventory changed'
  for f in snapshot['files']:
   p=project/f['path'];assert p.resolve().is_relative_to(project.resolve());assert p.stat().st_size==f['bytes'] and sha(p)==f['sha256'],'Source drift: '+str(p)
def receipts(project,ids,graph,selection):
 result=[]
 for identity in ids:
  p=project/'out'/f'{identity}.source-receipt.json';d=json.loads(p.read_text());assert d['sourceSnapshot']==graph and d['selection']==selection and d['selectedCompositionIds']==ids
  f=d['output'];assert f['path']==f'out/{identity}.mp4';m=project/f['path'];assert m.stat().st_size==f['bytes'] and sha(m)==f['sha256']
  result.append({'id':identity,'output':str(m),'sha256':sha(m),'receipt':str(p),'receiptSha256':sha(p)})
 return result

def preflight():
 assert re.sub('[^a-z0-9]','',socket.gethostname().split('.')[0].lower())=='jamessmacstudio'
 hp=RUNNER/'render-handoff.json';assert sha(hp)=='4094411058d337b4793592551f3659636f72dd2cd43644ef8b585c1d6446e6c4';handoff=json.loads(hp.read_text());assert len(handoff['jobs'])==1 and handoff['paidRequests']==0
 vp=Path(handoff['stagingVerification']['path']);assert sha(vp)==handoff['stagingVerification']['sha256']=='33315f12a7919eabfcb59b98b541733cefce004f1db10c22070d5a88bd50d039';v=json.loads(vp.read_text());assert (v['typecheck']['exitCode'] if 'typecheck' in v else v['typecheckExit'])==0
 mp=Path(v['runnerManifest']['path']);assert sha(mp)==v['runnerManifest']['sha256']=='01f24cc3eb282b2cb16383a60c96cce679f4548d2bfb01861aa77b6aaa7186ce';manifest=json.loads(mp.read_text());assert len(manifest['files'])==7
 for f in manifest['files']:assert sha(RUNNER/f['path'])==f['sha256'],'Runner helper drift'
 assert json.loads((RUNNER/'package.json').read_text())=={'type':'module'}
 assert (RUNNER/'node_modules').is_symlink() and (RUNNER/'node_modules').resolve()==(APP/'node_modules').resolve()
 snapshot_entry=v['preflightSnapshot'] if 'preflightSnapshot' in v else v['sourceSnapshot'];sp=Path(snapshot_entry['path']);assert sha(sp)==snapshot_entry['sha256']=='2f1dce24c0ded58a4dc64b4b686d0e5ca63455fc14e77f8e64c896cdd71cbaa7';raw=json.loads(sp.read_text());snapshots=[{'project':handoff['jobs'][0]['project'],'snapshot':raw['snapshot'] if 'snapshot' in raw else raw}]
 for job,(selection,ids,graph),snapshot in zip(handoff['jobs'],JOBS,snapshots):
  assert (job['selection'],job['compositionIds'],job['sourceFingerprint'])==(selection,ids,graph) and job['project']==snapshot['project'] and snapshot['snapshot']['fingerprint']==graph
  assert job['cwd']==str(APP) and job['argv']==[NODE,'--import','tsx',str(RUNNER/'scripts/render-teaching-corrections.ts'),'--project',job['project'],'--selection',selection,'--execute']
 snapshots_verify(snapshots)
 for item in v.get('exactVoiceAndPlanEvidence',[]):
  assert sha(Path(item['path']))==item['sha256'],'Voice proof drift'
 for row in [v['sourcePatches']]:
  p=Path(row['path']);assert sha(p)==row['sha256'];patch=json.loads(p.read_text());assert len(patch['files'])==3
  for f in patch['files']:assert sha(Path(patch['project'])/f['path'])==f['afterSha256']
  for f in patch.get('voiceProofs',[]):assert sha(Path(f['path']))==f['sha256']
 assert os.statvfs(RUN).f_bavail*os.statvfs(RUN).f_frsize>=50*1024**3,'Below50GiB floor'
 return handoff

def wait_predecessor(state):
 deadline=time.monotonic()+7200;p=RUN/'workflow/teaching-argument-dispatch-status.json'
 while True:
  if not p.exists():
   state.update(state='waiting-for-predecessor-status',checkedAt=now());save(state);time.sleep(20)
   if time.monotonic()>deadline:raise TimeoutError('Predecessor status absent for2hours')
   continue
  d=json.loads(p.read_text())
  if d['state']=='halted':raise ValueError('argument predecessor halted; explicit reconciliation required')
  if d['state']=='complete_rendered_needs_review':
   assert d['exitCode']==0 and [x['selection'] for x in d['completedSelections']]==[x[0]for x in PRIOR_JOBS]
   if not any(alive(d[k])for k in ['pid','renderControllerPid']if k in d):
    active=[]
    for line in subprocess.check_output(['ps','-axo','pid=,ppid=,pgid=,comm='],text=True).splitlines():
     parts=line.split(None,3)
     if len(parts)==4 and int(parts[2])==d['pid']:active.append({'pid':int(parts[0]),'ppid':int(parts[1]),'pgid':int(parts[2]),'comm':parts[3]})
    assert not active,'Predecessor process group still has active children'
    rows=[]
    for selection,ids,graph,project in PRIOR_JOBS:rows.extend(receipts(RUN/'teaching-repairs'/project,ids,graph,selection))
    assert len(rows)==2
    proof={'at':now(),'predecessorStatusSha256':sha(p),'outputs':rows,'parentsExited':True,'activePredecessorGroupChildren':active,'qualityApproval':False}
    target=RUN/'workflow/teaching-scale-predecessor-proof.json'
    if target.exists():assert json.loads(target.read_text())['outputs']==rows
    else:atomic(target,proof)
    return target
  if time.monotonic()>deadline:raise TimeoutError('argument completion wait exceeded2hours')
  state.update(state='waiting-for-argument',predecessorState=d['state'],checkedAt=now());save(state);time.sleep(20)

def lease_clear(previous=None):
 canonical=Path(subprocess.check_output(['/usr/bin/getconf','DARWIN_USER_TEMP_DIR'],text=True).strip());assert Path(os.environ['TMPDIR']).resolve()==canonical.resolve()
 lease=canonical/'smart-cub-founder-render.lock'
 if lease.exists() and previous:
  op=lease/'owner.json';raw=op.read_bytes();owner=json.loads(raw);assert owner['pid']==previous and not alive(previous) and owner['host']==socket.gethostname() and owner['cwd']==str(APP) and sorted(x.name for x in lease.iterdir())==['owner.json']
  archive=RUN/f'workflow/teaching-scale-dead-lease-{previous}.json'
  if archive.exists():assert archive.read_bytes()==raw
  else:archive.write_bytes(raw)
  assert op.read_bytes()==raw and not alive(previous);op.unlink();lease.rmdir()
 subprocess.run([str(RUN/'.venv/bin/python'),'-B',str(RUN/'workflow/render_tmpdir.py')],check=True)
 assert not lease.exists() and not (Path('/tmp')/'smart-cub-founder-render.lock').exists(),'Canonical or legacy lease remains'

def main():
 if '--check' in sys.argv:print(json.dumps({'state':'preflight_verified','jobs':preflight()['jobs']}));return
 STATUS.parent.mkdir(exist_ok=True)
 with STATUS.with_suffix('.lock').open('a+')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if STATUS.exists():raise ValueError('Existing successor checkpoint requires explicit reconciliation')
  state={'state':'waiting-for-argument','pid':os.getpid(),'startedAt':now(),'paidCalls':0,'qualityApproved':False,'jobs':1,'workers':4,'nice':10,'completedSelections':[]};save(state)
  try:
   prior=wait_predecessor(state);d=json.loads((RUN/'workflow/teaching-argument-dispatch-status.json').read_text());lease_clear(d.get('renderControllerPid'));handoff=preflight();state.update(predecessorProof=str(prior),predecessorProofSha256=sha(prior));save(state)
   for job in handoff['jobs']:
    preflight();lease_clear();state.update(state='rendering',selection=job['selection'],project=job['project'],sourceFingerprint=job['sourceFingerprint']);save(state)
    child=subprocess.Popen(job['argv'],cwd=APP);state['renderControllerPid']=child.pid;save(state);code=child.wait()
    if code:raise RuntimeError(f"Successor selection{job['selection']} exited{code}; no automatic retry")
    proof=receipts(Path(job['project']),job['compositionIds'],job['sourceFingerprint'],job['selection']);lease_clear(child.pid);state['completedSelections'].append({'selection':job['selection'],'outputs':proof});state.pop('renderControllerPid',None);save(state)
   state.update(state='complete_rendered_needs_review',exitCode=0,finishedAt=now());save(state)
  except BaseException as e:
   state.update(state='halted',errorType=type(e).__name__,error=str(e)[:500],automaticRetry=False,finishedAt=now());save(state);raise
if __name__=='__main__':main()
