#!/usr/bin/env python3
"""Exact fixed-pair source rendering after explicit ordinary-scope release."""
import datetime,fcntl,hashlib,json,os,re,socket,subprocess,sys,time
from pathlib import Path
RUN=Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
APP=RUN/'revision/smart-cub/apps/prototypes/founder-videos'
CONFIG_SHA='2e99ea6cbd2a457b384c261c3759254dfebebdfedaf2a2b3922927f2d1f3a9f5'
CONFIG_PATH=Path(__file__).with_name('config.json')
NODE='/opt/homebrew/opt/node@22/bin/node'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def atomic(p,value):
 temp=p.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n')
 with temp.open('rb')as f:os.fsync(f.fileno())
 os.replace(temp,p)
def config():
 assert sha(CONFIG_PATH)==CONFIG_SHA,'Dispatcher configuration changed'
 return json.loads(CONFIG_PATH.read_text())
def verify_ref(ref):
 p=Path(ref['path']);assert p.stat().st_size==ref['bytes'] and sha(p)==ref['sha256'],'Pinned evidence changed: '+str(p)
 return p
def checked(ref):return json.loads(verify_ref(ref).read_text())
def inventory(project,snapshot):
 actual=set()
 for folder in ['src','data','public']:
  assert (project/folder).is_dir()
  for p in (project/folder).rglob('*'):
   assert not p.is_symlink(),'Source symlink unsupported'
   if p.is_file():actual.add(str(p.relative_to(project)))
 actual.update(['package.json','package-lock.json','tsconfig.json'])
 assert actual=={x['path']for x in snapshot['files']},'Source inventory changed'
 for f in snapshot['files']:
  p=project/f['path'];assert p.resolve().is_relative_to(project.resolve()) and p.stat().st_size==f['bytes'] and sha(p)==f['sha256'],'Source input changed: '+f['path']
def receipts(job):
 project=Path(job['project']);result=[]
 for identity in job['compositionIds']:
  p=project/'out'/f'{identity}.source-receipt.json';j=json.loads(p.read_text());assert j['sourceSnapshot']==job['sourceFingerprint'] and j['selection']==job['selection'] and j['selectedCompositionIds']==job['compositionIds']
  f=j['output'];assert f['path']==f'out/{identity}.mp4';media=project/f['path'];assert media.stat().st_size==f['bytes'] and sha(media)==f['sha256']
  result.append({'id':identity,'output':str(media),'sha256':sha(media),'receipt':str(p),'receiptSha256':sha(p)})
 return result
def process_clear(status):
 keys=['pid','dispatcherPgid','renderControllerPid','renderControllerPgid'];ids=[status[k]for k in keys];assert all(type(x)is int and x>0 for x in ids),'Missing real predecessor process identities'
 rows=[x.split()for x in subprocess.check_output(['ps','-axo','pid=,ppid=,pgid='],text=True).splitlines()if x.strip()]
 active=[x for x in rows if int(x[0])in ids or int(x[1])in ids or int(x[2])in ids]
 assert not active,'Predecessor owner, descendant or process-group member remains active'
 return {k:status[k]for k in keys}
def preflight(c):
 assert re.sub('[^a-z0-9]','',socket.gethostname().split('.')[0].lower())=='jamessmacstudio'
 hp=checked(c['handoff']);assert hp['paidRequests']==0 and hp['qualityApproved']is False
 job=hp['jobs'][c['jobIndex']];assert job==c['job'],'Fixed selected job changed'
 assert job['cwd']==str(APP) and job['argv']==[NODE,'--import','tsx',str(Path(c['runner'])/'scripts/render-teaching-corrections.ts'),'--project',job['project'],'--selection',job['selection'],'--execute'] and len(job['compositionIds'])==2
 v=checked(c['staging']);assert v['typecheck']['exitCode']==0 and v['sourceFingerprint']==job['sourceFingerprint'] and v['originalParentUnchanged']is True and v['paidRequests']==0 and len(v['exactChangedFiles'])==3
 checked(v['approvedPlan']);verify_ref(v['typecheck']['log'])
 patch=checked(v['sourcePatches']);assert len(patch['files'])==3 and {x['path']for x in patch['files']}==set(v['exactChangedFiles'])
 project=Path(job['project'])
 for f in patch['files']:assert sha(project/f['path'])==f['afterSha256']
 raw=checked(v['preflightSourceSnapshot']);snapshot=raw.get('snapshot',raw);assert snapshot['fingerprint']==job['sourceFingerprint'];inventory(project,snapshot)
 manifest=checked(c['runnerManifest']);assert len(manifest['files'])==9
 runner=Path(c['runner'])
 for f in manifest['files']:
  p=runner/f['path'];assert p.resolve().is_relative_to(runner.resolve()) and p.stat().st_size==f['bytes'] and sha(p)==f['sha256'],'Runner helper changed'
 assert sha(runner/'package.json')==manifest['packageSha256'] and json.loads((runner/'package.json').read_text())=={'type':'module'}
 assert (runner/'node_modules').is_symlink() and (runner/'node_modules').resolve()==(APP/'node_modules').resolve()
 assert os.statvfs(RUN).f_bavail*os.statvfs(RUN).f_frsize>=50*1024**3,'Below50GiB floor'
 return job
def assert_empty_log(ref):
 p=Path(ref['path']);assert p.stat().st_size==0 and sha(p)==ref['sha256']
def released(c):
 p=RUN/'workflow/ordinary-teaching-source-successors-release-20261016.json'
 d=json.loads(p.read_text());assert d['sourceRenderRelease']is True and d['paidRequests']==0
 assert c['job'] in d['jobs'],'This fixed source pair is not released'
 checked(d['ordinaryClosureAudit'])
 return {'path':str(p),'sha256':sha(p)}
def wait_prior(c,state,status):
 target=RUN/'workflow'/c['predecessorStatus'];deadline=time.monotonic()+7200
 while True:
  if time.monotonic()>deadline:raise TimeoutError('Predecessor not normally complete within two hours')
  if target.exists():
   d=json.loads(target.read_text())
   if d['state']=='halted':raise ValueError('Predecessor halted; explicit reconciliation required')
   if d['state']=='complete_rendered_needs_review':
    assert d['exitCode']==0 and [x['selection']for x in d['completedSelections']]==[c['predecessor']['selection']]
    try:identities=process_clear(d)
    except AssertionError:
     time.sleep(2);continue
    rows=receipts(c['predecessor']);proof={'at':now(),'status':{'path':str(target),'sha256':sha(target)},'identities':identities,'noLiveOwnerDescendantOrGroupMember':True,'outputs':rows,'qualityApproved':False}
    p=RUN/'workflow'/c['predecessorProof']
    if p.exists():assert json.loads(p.read_text())['outputs']==rows
    else:atomic(p,proof)
    return {'path':str(p),'sha256':sha(p)}
  if time.monotonic()>deadline:raise TimeoutError('Predecessor not normally complete within two hours')
  state.update(state='waiting-for-predecessor',checkedAt=now());atomic(status,state);time.sleep(20)
def leases_clear():
 canonical=Path(subprocess.check_output(['/usr/bin/getconf','DARWIN_USER_TEMP_DIR'],text=True).strip());assert Path(os.environ['TMPDIR']).resolve()==canonical.resolve()
 subprocess.run([str(RUN/'.venv/bin/python'),'-B',str(RUN/'workflow/render_tmpdir.py')],check=True)
 assert not (canonical/'smart-cub-founder-render.lock').exists()and not Path('/tmp/smart-cub-founder-render.lock').exists(),'Canonical or legacy render lease remains; no automatic lock deletion'
def main():
 c=config()
 if '--check'in sys.argv:print(json.dumps({'state':'pinned_preflight_verified_not_started','job':preflight(c),'releaseRequiredForExecute':True}));return
 assert sys.argv[1:]==['--execute'],'Explicit --execute required'
 release=released(c);status=RUN/'workflow'/c['status']
 with status.with_suffix('.lock').open('a+')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);assert not status.exists(),'Existing checkpoint needs explicit reconciliation'
  state={'state':'waiting-for-predecessor','pid':os.getpid(),'dispatcherPgid':os.getpgid(0),'startedAt':now(),'paidCalls':0,'qualityApproved':False,'workers':4,'nice':10,'completedSelections':[],'release':release};atomic(status,state)
  try:
   state['predecessorProof']=wait_prior(c,state,status);leases_clear();job=preflight(c);assert released(c)==release
   state.update(state='rendering',selection=job['selection'],project=job['project'],sourceFingerprint=job['sourceFingerprint']);atomic(status,state)
   child=subprocess.Popen(job['argv'],cwd=APP);state.update(renderControllerPid=child.pid,renderControllerPgid=os.getpgid(child.pid));atomic(status,state);code=child.wait()
   assert code==0,'Renderer failed; no automatic retry'
   proof=receipts(job);leases_clear();preflight(c);state['completedSelections']=[{'selection':job['selection'],'outputs':proof,'controllerPid':child.pid,'controllerPgid':state['renderControllerPgid'],'dispatcherPgid':state['dispatcherPgid']}];state.update(state='complete_rendered_needs_review',exitCode=0,finishedAt=now());atomic(status,state)
  except BaseException as e:
   state.update(state='halted',errorType=type(e).__name__,error=str(e)[:500],automaticRetry=False,finishedAt=now());atomic(status,state);raise
if __name__=='__main__':main()
