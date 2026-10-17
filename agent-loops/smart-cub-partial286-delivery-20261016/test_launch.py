import importlib.util,tempfile,unittest
from pathlib import Path
from unittest import mock
class DeliveryLaunchTests(unittest.TestCase):
 def module(self):
  s=importlib.util.spec_from_file_location('launch286',Path(__file__).with_name('launch.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 def test_exact_wrapper_only_and_no_double_nice(self):
  m=self.module()
  with tempfile.TemporaryDirectory()as td:
   p=Path(td)/'wrapper.py';p.write_text('metadata fixture')
   with mock.patch.object(m,'WRAPPER',p),mock.patch.object(m,'EXPECTED',m.sha(p)),mock.patch.object(m.socket,'gethostname',return_value='JamessMacStudio.fritz.box'):
    self.assertEqual(m.command(),[str(m.PYTHON),'-B',str(p)]);p.write_text('changed')
    with self.assertRaisesRegex(ValueError,'bytes changed'):m.command()
 def test_wrong_host_refuses_before_exec(self):
  m=self.module()
  with mock.patch.object(m.socket,'gethostname',return_value='MacBook-Pro'),mock.patch.object(m.os,'execv')as execute,mock.patch.object(m.sys,'argv',['launch.py','--execute']):
   with self.assertRaisesRegex(ValueError,'Studio'):m.main()
   execute.assert_not_called()
if __name__=='__main__':unittest.main()
