import importlib.util,json,tempfile,unittest,os
from pathlib import Path
from unittest import mock
BASE=Path(__file__).parent
class SuccessorTests(unittest.TestCase):
 def module(self,short):
  p=BASE/f'smart-cub-teaching-{short}-20261016'/'render.py';s=importlib.util.spec_from_file_location(short,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 def test_three_fixed_pairs_and_serial_predecessor_chain(self):
  prior='rotate-markers'
  for short in ['transfer-ticket','shift-sheet-label','cloze-context']:
   m=self.module(short);c=m.config();self.assertEqual(len(c['job']['compositionIds']),2);self.assertEqual(c['predecessorStatus'],f'teaching-{prior}-dispatch-status.json');self.assertEqual(c['job']['selection'],short);prior=short
 def test_derived_fixed_pair_waits_cloze_and_uses_separate_release(self):
  m=self.module('derived-word-spacing');c=m.config();self.assertEqual(c['predecessor']['selection'],'cloze-context');self.assertEqual(c['job']['compositionIds'],['v3-y6-form-derived-word-wide','v3-y6-form-derived-word-tall']);self.assertEqual(c['job']['sourceFingerprint'],'545e8182b9aef0b426483ca1abacec5ef44b1696dc793e030b5e8af26b96535e')
  self.assertIn('derived-word-source-successor-release-20261016.json',Path(m.__file__).read_text());self.assertNotIn('ordinary-teaching-source-successors-release-20261016.json',Path(m.__file__).read_text())
 def test_config_tamper_refused(self):
  m=self.module('transfer-ticket')
  with mock.patch.object(m,'CONFIG_SHA','wrong'):
   with self.assertRaisesRegex(AssertionError,'configuration'):m.config()
 def test_missing_explicit_release_never_launches_or_creates_status(self):
  m=self.module('transfer-ticket')
  with tempfile.TemporaryDirectory()as td,mock.patch.object(m,'RUN',Path(td)),mock.patch.object(m.sys,'argv',['render.py','--execute']),mock.patch.object(m.subprocess,'Popen')as launch:
   with self.assertRaises(FileNotFoundError):m.main()
   launch.assert_not_called();self.assertFalse(list(Path(td).glob('*status*')))
 def test_release_binds_exact_job_and_closure_audit_hash(self):
  m=self.module('transfer-ticket');c=m.config()
  with tempfile.TemporaryDirectory()as td,mock.patch.object(m,'RUN',Path(td)):
   p=Path(td);(p/'workflow').mkdir();audit=p/'audit.json';audit.write_text('{}');release=p/'workflow/ordinary-teaching-source-successors-release-20261016.json';d={'sourceRenderRelease':True,'paidRequests':0,'jobs':[c['job']],'ordinaryClosureAudit':{'path':str(audit),'bytes':2,'sha256':m.sha(audit)}};release.write_text(json.dumps(d));self.assertTrue(m.released(c));audit.write_text('{"changed":true}')
   with self.assertRaisesRegex(AssertionError,'evidence changed'):m.released(c)
 def test_complete_inventory_rejects_extra_and_changed_file(self):
  m=self.module('transfer-ticket')
  with tempfile.TemporaryDirectory()as td:
   p=Path(td)
   for f in ['src','data','public']:(p/f).mkdir()
   for f in ['src/lesson.ts','package.json','package-lock.json','tsconfig.json']:(p/f).write_text('fixture')
   snapshot={'files':[{'path':str(f.relative_to(p)),'bytes':f.stat().st_size,'sha256':m.sha(f)}for f in p.rglob('*')if f.is_file()]};m.inventory(p,snapshot);extra=p/'src/extra.ts';extra.write_text('extra')
   with self.assertRaisesRegex(AssertionError,'inventory changed'):m.inventory(p,snapshot)
   extra.unlink();(p/'src/lesson.ts').write_text('drift')
   with self.assertRaisesRegex(AssertionError,'input changed'):m.inventory(p,snapshot)
 def test_missing_controller_metadata_refused(self):
  m=self.module('transfer-ticket')
  with self.assertRaises(KeyError):m.process_clear({'pid':1})
 def test_dead_parent_does_not_hide_live_controller_group(self):
  m=self.module('transfer-ticket');d={'pid':111,'dispatcherPgid':222,'renderControllerPid':333,'renderControllerPgid':444}
  with mock.patch.object(m.subprocess,'check_output',return_value='555 1 444\n'):
   with self.assertRaisesRegex(AssertionError,'process-group'):m.process_clear(d)
  with mock.patch.object(m.subprocess,'check_output',return_value='999 1 999\n'):self.assertEqual(m.process_clear(d),d)
 def test_atomic_receipt_binds_source_identity_and_output_bytes(self):
  m=self.module('transfer-ticket')
  with tempfile.TemporaryDirectory()as td:
   p=Path(td);(p/'out').mkdir();j={'project':str(p),'selection':'fixed','compositionIds':['wide','tall'],'sourceFingerprint':'exact'}
   for name in j['compositionIds']:
    f=p/'out'/f'{name}.mp4';f.write_bytes(b'non-media hash fixture');f.with_suffix('.source-receipt.json').write_text(json.dumps({'sourceSnapshot':'exact','selection':'fixed','selectedCompositionIds':j['compositionIds'],'output':{'path':str(f.relative_to(p)),'bytes':f.stat().st_size,'sha256':m.sha(f)}}))
   self.assertEqual(len(m.receipts(j)),2);f.write_bytes(b'different')
   with self.assertRaises(AssertionError):m.receipts(j)
 def test_failed_predecessor_never_admits(self):
  m=self.module('transfer-ticket');c=m.config()
  with tempfile.TemporaryDirectory()as td,mock.patch.object(m,'RUN',Path(td)),mock.patch.object(m.subprocess,'Popen')as launch:
   p=Path(td);(p/'workflow').mkdir();(p/'workflow'/c['predecessorStatus']).write_text(json.dumps({'state':'halted'}))
   with self.assertRaisesRegex(ValueError,'explicit reconciliation'):m.wait_prior(c,{},p/'status.json')
   launch.assert_not_called()
if __name__=='__main__':unittest.main()
