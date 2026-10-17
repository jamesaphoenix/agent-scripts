import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest import mock
s=importlib.util.spec_from_file_location('followon',Path(__file__).with_name('render.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class AdmissionTests(unittest.TestCase):
 def test_fixed_three_and_exact_predecessor_ten_receipts_reject_drift(self):
  self.assertEqual([len(x[1])for x in m.JOBS],[1,2]);self.assertEqual(len(m.PRIOR_IDS),10)
  with tempfile.TemporaryDirectory()as tmp:
   p=Path(tmp);(p/'out').mkdir()
   for identity in m.PRIOR_IDS:
    media=p/'out'/f'{identity}.mp4';media.write_bytes(b'exact metadata fixture')
    (media.with_suffix('.source-receipt.json')).write_text(json.dumps({'sourceSnapshot':m.GRAPH,'selection':'final-five','selectedCompositionIds':m.PRIOR_IDS,'output':{'path':str(media.relative_to(p)),'bytes':media.stat().st_size,'sha256':m.sha(media)}}))
   self.assertEqual(len(m.receipts(p,m.PRIOR_IDS,m.GRAPH,'final-five')),10)
   media.write_bytes(b'drift')
   with self.assertRaises(AssertionError):m.receipts(p,m.PRIOR_IDS,m.GRAPH,'final-five')
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
   r=Path(tmp);(r/'workflow').mkdir();(r/'workflow/teaching-final-five-dispatch-status.json').write_text(json.dumps({'state':'halted'}))
   with mock.patch.object(m,'RUN',r),mock.patch.object(m.subprocess,'Popen')as launch:
    with self.assertRaisesRegex(ValueError,'explicit reconciliation'):m.wait_predecessor({})
    launch.assert_not_called()
if __name__=='__main__':unittest.main()
