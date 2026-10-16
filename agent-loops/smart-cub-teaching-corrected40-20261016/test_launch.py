import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest import mock
class LaunchTests(unittest.TestCase):
 def module(self):
  s=importlib.util.spec_from_file_location('launch40',Path(__file__).with_name('launch.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 def fixture(self,m,root):
  script=root/'blend-corrected-batch.py';script.write_text('not executed');plan=root/'plan.json';plan.write_text('{}');ref=lambda p:{'path':str(p),'bytes':p.stat().st_size,'sha256':m.sha(p)};config=root/'approved-batch.json';config.write_text(json.dumps({'contract':'smart-cub-corrected40-launch/v1','script':ref(script),'plan':ref(plan),'status':str(root/'status.json')}));return config,script,plan
 def test_missing_approved_plan_binding_never_executes(self):
  m=self.module()
  with tempfile.TemporaryDirectory()as td,mock.patch.object(m,'CONFIG',Path(td)/'missing.json'),mock.patch.object(m.socket,'gethostname',return_value='JamessMacStudio.fritz.box'),mock.patch.object(m.os,'execv')as execute,mock.patch.object(m.sys,'argv',['launch.py','--execute']):
   with self.assertRaises(FileNotFoundError):m.main()
   execute.assert_not_called()
 def test_exact_argument_only_launch_and_changed_plan_refused(self):
  m=self.module()
  with tempfile.TemporaryDirectory()as td:
   r=Path(td);config,script,plan=self.fixture(m,r)
   with mock.patch.object(m,'RUN',r),mock.patch.object(m,'CONFIG',config),mock.patch.object(m.socket,'gethostname',return_value='JamessMacStudio.fritz.box'):
    result=m.command();self.assertEqual(result,[str(m.PYTHON),'-B',str(script),'run','--plan',str(plan),'--plan-sha256',m.sha(plan),'--status',str(r/'status.json'),'--execute']);plan.write_text('{"drift":true}')
    with self.assertRaisesRegex(ValueError,'Pinned bytes'):m.command()
 def test_installer_does_not_rewrite_shared_tmpdir_policy(self):
  source=Path(__file__).with_name('install-launchd.sh').read_text();self.assertNotIn('render_tmpdir.py',source)
 def test_nonstudio_host_and_outside_status_refused(self):
  m=self.module()
  with mock.patch.object(m.socket,'gethostname',return_value='MacBook-Pro'):
   with self.assertRaisesRegex(ValueError,'Studio'):m.command()
  with tempfile.TemporaryDirectory()as td:
   r=Path(td);config,_,_=self.fixture(m,r);j=json.loads(config.read_text());j['status']='/outside/status.json';config.write_text(json.dumps(j))
   with mock.patch.object(m,'RUN',r),mock.patch.object(m,'CONFIG',config),mock.patch.object(m.socket,'gethostname',return_value='JamessMacStudio.fritz.box'):
    with self.assertRaisesRegex(ValueError,'Status'):m.command()
if __name__=='__main__':unittest.main()
