import ast
import os
import unittest
from pathlib import Path

class LaunchdDomainTests(unittest.TestCase):
    def target(self,task):
        source=(Path(__file__).resolve().parents[1]/'scripts/install-launchd-tasks.sh').read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='launchd_target')
        scope={'Any':object,'os':os};exec(compile(ast.Module(body=[function],type_ignores=[]),'installer','exec'),scope)
        return scope['launchd_target'](task)
    def test_existing_jobs_keep_gui_domain(self):
        self.assertEqual(self.target({}),f'gui/{os.getuid()}')
    def test_headless_job_uses_same_uid_user_domain(self):
        self.assertEqual(self.target({'launchdDomain':'user'}),f'user/{os.getuid()}')
    def test_system_or_other_uid_domains_are_rejected(self):
        for domain in ['system','user/0','gui/0','user/other']:
            with self.assertRaises(SystemExit):self.target({'launchdDomain':domain})
if __name__=='__main__':unittest.main()
