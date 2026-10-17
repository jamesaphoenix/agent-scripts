import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).with_name('render.py'); s=importlib.util.spec_from_file_location('render_recovery',p); m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class RecoveryTests(unittest.TestCase):
 def test_partition_has26_disjoint_ten_output_controllers(self):
  ids=['id'+str(i) for i in range(130)]; c=m.chunks(ids)
  self.assertEqual(len(c),26); self.assertTrue(all(len(x)==5 for x in c)); self.assertEqual([i for x in c for i in x],ids)
 def test_duplicate_or_incomplete_scope_refused(self):
  for ids in [['id']*130,['id'+str(i) for i in range(129)],['id'+str(i) for i in range(129)]+['bad,other']]:
   with self.assertRaises(ValueError): m.chunks(ids)
 def test_actual_native_argv_keeps_eight_gib_two_by_four(self):
  cmd=m.command(['one','two']); self.assertIn('--max-old-space-size=8192',cmd); self.assertEqual(cmd[cmd.index('--ids')+1],'one,two'); self.assertEqual(cmd[-5:],['--jobs','2','--workers','4','--execute'])
 def test_plan_only_has_no_execute(self):
  self.assertNotIn('--execute',m.command(['one'],False))
 def test_oversize_or_duplicate_controller_refused(self):
  for ids in [[],['a']*2,list('abcdef')]:
   with self.assertRaises(ValueError):m.command(ids)
 def test_exact_owner_descendant_and_orphaned_group_refused(self):
  for rows in [[(97249,1,97249)],[(77,11218,77)],[(88,1,11218)]]:
   with self.assertRaises(ValueError):m.dead_groups([97249,11218],rows)
 def test_unrelated_process_preserved(self):
  self.assertIsNone(m.dead_groups([97249,11218],[(77,1,77)]))
 def test_invalid_owner_identity_refused(self):
  for ids in [[0,11218],[97249,97249]]:
   with self.assertRaises(ValueError):m.dead_groups(ids,[])
 def test_every_preserved_receipt_requires_actual_skip_and_group_clear(self):
  src=p.read_text(); self.assertIn("('SKIP verified ' + prior['id']) not in log_text.splitlines()",src); self.assertIn('any(child.pid in row for row in processes())',src)
 def test_no_legacy_status_write_or_paid_command(self):
  src=p.read_text(); self.assertIn("originalFailedStatusRewritten=False",src); self.assertNotIn("original.save(Path(plan['originalFailedStatus']",src); self.assertNotIn('review-media.py',src)



import copy
import tempfile
spec=importlib.util.spec_from_file_location('reconciler',p.with_name('reconcile.py')); r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class TerminalReconciliationTests(unittest.TestCase):
 def fixture(self):
  ids=['p9-after-a-bad-mock','t29-ratio-as-shares']+['id'+str(i) for i in range(128)]
  parts=m.chunks(ids); fresh={i:{a:{'output':{'sha256':i+a},'receipt':{'sha256':i+a+'receipt'}} for a in ['wide','tall']} for i in ids}
  old=[{'id':i+'-'+a} for i in ids[:45] for a in ['wide','tall']]+[{'id':ids[45]+'-wide'}]
  plan={'chunks':parts,'preservedOutputs':old}; chunks=[];logs=[]
  for n,items in enumerate(parts):
   chunks.append({'index':n,'ids':items,'controllerPid':20000+n,'controllerPgid':20000+n,'outputs':{i+':'+a:fresh[i][a] for i in items for a in ['wide','tall']}})
   logs.append('\n'.join('SKIP verified '+i+'-'+a for i in items for a in ['wide','tall']))
  term={'state':'recovered_complete260_rendered_verified_needs_quality_review','qualityApproved':False,'pid':10000,'dispatcherPgid':10000,'completedChunks':chunks,'outputs':{i+':'+a:fresh[i][a] for i in ids for a in ['wide','tall']}}
  return plan,term,fresh,logs
 def test_fresh_exact260_terminal_accepted(self):
  p,t,f,l=self.fixture();self.assertEqual(len(r.validate_terminal(p,t,f,l,[])),260)
 def test_changed_nonpreserved_current_output_refused(self):
  p,t,f,l=self.fixture();f=copy.deepcopy(f);f['id127']['wide']['output']['sha256']='changed'
  with self.assertRaises(ValueError):r.validate_terminal(p,t,f,l,[])
 def test_stale_or_missing_current_output_refused(self):
  p,t,f,l=self.fixture();t['outputs'].pop('id127:tall')
  with self.assertRaises(ValueError):r.validate_terminal(p,t,f,l,[])
 def test_native_pilot_skip_missing_refused(self):
  p,t,f,l=self.fixture();l[0]=l[0].replace('SKIP verified t29-ratio-as-shares-wide','NO SKIP')
  with self.assertRaises(ValueError):r.validate_terminal(p,t,f,l,[])
 def test_any_prior_controller_group_still_live_refused(self):
  p,t,f,l=self.fixture()
  with self.assertRaises(ValueError):r.validate_terminal(p,t,f,l,[(90000,1,20005)])
 def test_reordered_chunk_refused(self):
  p,t,f,l=self.fixture();t['completedChunks'][3]['index']=5
  with self.assertRaises(ValueError):r.validate_terminal(p,t,f,l,[])
 def test_changed_original_status_cas_never_writes(self):
  with tempfile.TemporaryDirectory() as d:
   x=Path(d)/'status.json';x.write_text('original');expected=m.sha(x);x.write_text('another writer');called=[]
   with self.assertRaises(ValueError):r.cas_publish(x,expected,{},lambda *args:called.append(args))
   self.assertEqual(called,[]);self.assertEqual(x.read_text(),'another writer')
 def test_exact_original_status_cas_single_publication(self):
  with tempfile.TemporaryDirectory() as d:
   x=Path(d)/'status.json';x.write_text('original');called=[]
   r.cas_publish(x,m.sha(x),{'state':r.TARGET_STATE},lambda *args:called.append(args));self.assertEqual(len(called),1)

 def test_post_publication_error_preserves_new_status_without_rollback(self):
  with tempfile.TemporaryDirectory() as d:
   x=Path(d)/'status.json';x.write_text('original');expected=m.sha(x)
   def publish_then_fail(path,value):
    path.write_text(value['state'])
    raise OSError('simulated directory-fsync or completion-receipt failure')
   with self.assertRaises(OSError):r.cas_publish(x,expected,{'state':r.TARGET_STATE},publish_then_fail)
   self.assertEqual(x.read_text(),r.TARGET_STATE)

if __name__=='__main__': unittest.main()
