#!/opt/homebrew/bin/python3
"""One authorised execution, unchanged strict delivery helper; no authority writes."""
import hashlib,json,os,socket,subprocess,time,traceback
from pathlib import Path
RUN=Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
PLAN=RUN/'workflow/partial286-delivery-plan-20261016.json'
EXPECTED='02f4747555b06614f4af13312116d418fba29004e839b0ff11a722a9c9ecd454'
STATUS=RUN/'workflow/partial286-delivery-execute-status.json'
RESULT=RUN/'evidence/partial286-delivery-execute-result.json'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
 return h.hexdigest()
def save(p,v):
 t=p.with_name(p.name+'.tmp-'+str(os.getpid())); t.write_text(json.dumps(v,indent=2)+'\n'); os.replace(t,p)
base={'contract':'partial286-delivery-execution/v1','pid':os.getpid(),'started':time.time(),'plan':str(PLAN),'planSha256':EXPECTED,'rendered':False,'providerCalled':False,'authorityWritten':False}
try:
 assert ''.join(c for c in socket.gethostname().split('.')[0].lower() if c.isalnum())=='jamessmacstudio','Studio required'
 assert sha(PLAN)==EXPECTED,'Plan changed'
 p=json.loads(PLAN.read_text()); args=p['futureExecuteCommandArgv']; helper=Path(args[1]); dest=Path(p['existingDelivery']['destination'])
 assert sha(helper)==p['deliveryHelperSha256']=='70dba63b31a2a881a34bb2e5797884e39de2f50b6ac57b5362c2c0412dd7d52e'
 assert sha(helper.parent/'prepare-review.py')==p['prepareHelperSha256']
 assert p['selected_output_count']==286 and len(p['ids'])==143 and p['total_requested_outputs']==586 and p['all_requested_outputs_included'] is False
 assert '--execute' in args and args[args.index('--ids')+1:args.index('--aspect')]==p['ids']
 assert os.readlink(dest/'current')==p['existingDelivery']['currentTarget'],'Unexpected current generation'
 for path,digest in p['existingDelivery']['preservedFiles'].items(): assert sha(path)==digest,path
 assert not RESULT.exists(),'No automatic repeat execution'
 base.update(state='strict_preflight_and_delivery_running',commandArgv=args,oldCurrent=os.readlink(dest/'current'))
 save(STATUS,base)
 result=subprocess.run(['/usr/bin/nice','-n','10',*args],cwd=str(RUN),capture_output=True,text=True)
 raw={'exitCode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
 save(RESULT,raw)
 if result.returncode: raise RuntimeError('Strict helper failed: '+result.stderr[-3000:])
 value=json.loads(result.stdout)
 assert value['state']=='selected_approved_delivery_activated'
 generation=dest/'generations'/value['generation']
 assert os.readlink(dest/'current')=='generations/'+value['generation']
 m=json.loads((generation/'manifest.json').read_text()); assert len(m['videos'])==286 and m['scope']['total_requested_outputs']==586 and m['all_requested_outputs_included'] is False
 assert m['ledger_snapshot_read_only'] is True and m['ledger_snapshot_is_budget_authority'] is False
 for path,digest in p['existingDelivery']['preservedFiles'].items(): assert sha(path)==digest,'Prior generation changed '+path
 base.update(state='activated',finished=time.time(),result=value,resultSha256=sha(RESULT),manifestPath=str(generation/'manifest.json'),manifestSha256=sha(generation/'manifest.json'),oldGenerationPreserved=True)
 save(STATUS,base)
except BaseException as exc:
 base.update(state='failed',finished=time.time(),error=str(exc),traceback=traceback.format_exc());save(STATUS,base);raise
