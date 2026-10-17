import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest import mock
s=importlib.util.spec_from_file_location('scale',Path(__file__).with_name('render.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class AdmissionTests(unittest.TestCase):
 def test_completed_controller_identity_remains_available_for_successor(self):
  state={'completedSelections':[]}
  with mock.patch.object(m.os,'getpgid',side_effect=lambda pid:1234):
   m.record_controller(state,type('Child',(),{'pid':5678})())
  m.record_selection_complete(state,{'selection':'rotate-markers'},[{'id':'fixture'}])
  self.assertEqual(state['renderControllerPid'],5678)
  self.assertEqual(state['renderControllerPgid'],1234)
  self.assertEqual(state['dispatcherPgid'],1234)
  self.assertEqual(state['completedSelections'][0]['controllerPid'],5678)
  self.assertEqual(state['completedSelections'][0]['controllerPgid'],1234)

 def test_fixed_two_after_exact_predecessor_pair_rejects_receipt_drift(self):
  self.assertEqual([len(x[1])for x in m.JOBS],[2]);self.assertEqual([len(x[1])for x in m.PRIOR_JOBS],[2])
  with tempfile.TemporaryDirectory()as tmp:
   p=Path(tmp);(p/'out').mkdir();selection,ids,graph,_=m.PRIOR_JOBS[0]
   for identity in ids:
    media=p/'out'/f'{identity}.mp4';media.write_bytes(b'exact metadata fixture')
    media.with_suffix('.source-receipt.json').write_text(json.dumps({'sourceSnapshot':graph,'selection':selection,'selectedCompositionIds':ids,'output':{'path':str(media.relative_to(p)),'bytes':media.stat().st_size,'sha256':m.sha(media)}}))
   self.assertEqual(len(m.receipts(p,ids,graph,selection)),2)
   receipt=media.with_suffix('.source-receipt.json');data=json.loads(receipt.read_text());data['sourceSnapshot']='wrong';receipt.write_text(json.dumps(data))
   with self.assertRaises(AssertionError):m.receipts(p,ids,graph,selection)
 def test_full_staged_snapshot_rejects_changed_input(self):
  with tempfile.TemporaryDirectory()as tmp:
   p=Path(tmp)
   for name in ['src','data','public']:(p/name).mkdir()
   f=p/'src/source.ts';f.write_text('source')
   for name in ['package.json','package-lock.json','tsconfig.json']:(p/name).write_text('fixture')
   files=[f,p/'package.json',p/'package-lock.json',p/'tsconfig.json']
   rows=[{'project':str(p),'snapshot':{'files':[{'path':str(z.relative_to(p)),'bytes':z.stat().st_size,'sha256':m.sha(z)}for z in files]}}];m.snapshots_verify(rows)
   extra=p/'src/new.ts';extra.write_text('unapproved addition')
   with self.assertRaisesRegex(AssertionError,'file inventory changed'):m.snapshots_verify(rows)
   extra.unlink();f.write_text('drift')
   with self.assertRaises(AssertionError):m.snapshots_verify(rows)
 def test_failed_predecessor_never_admits_successor(self):
  with tempfile.TemporaryDirectory()as tmp:
   r=Path(tmp);(r/'workflow').mkdir();(r/'workflow/teaching-scale-dispatch-status.json').write_text(json.dumps({'state':'halted'}))
   with mock.patch.object(m,'RUN',r),mock.patch.object(m.subprocess,'Popen')as launch:
    with self.assertRaisesRegex(ValueError,'explicit reconciliation'):m.wait_predecessor({})
    launch.assert_not_called()
if __name__=='__main__':unittest.main()
