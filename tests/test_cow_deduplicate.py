import importlib.util,os,subprocess,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('cow',Path(__file__).resolve().parents[1]/'agent-loops/cache-hygiene/cow-deduplicate.py')
cow=importlib.util.module_from_spec(spec);spec.loader.exec_module(cow)
class CloneTests(unittest.TestCase):
 def test_clone_preserves_target_metadata_and_independent_edits(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();source=root/'source.json';target=root/'target.json';content=b'x'*1048576;source.write_bytes(content);target.write_bytes(content)
   subprocess.run(['xattr','-w','user.canonical','source',str(source)],check=True);subprocess.run(['xattr','-w','user.target','target',str(target)],check=True)
   subprocess.run(['chmod','+a','everyone allow read',str(source)],check=True)
   os.chmod(target,0o600);os.utime(target,(1600000000,1600000000));before=target.stat();birth=cow.creation_time(target);attrs=subprocess.check_output(['xattr','-lx',str(target)])
   self.assertNotEqual(cow.creation_time(source),birth)
   acl=subprocess.check_output(['ls','-le',str(target)]).splitlines()[1:]
   cow.clone_replace(source,target,cow.digest(source),root)
   self.assertEqual(target.read_bytes(),content);self.assertEqual(target.stat().st_mode,before.st_mode);self.assertEqual(target.stat().st_mtime_ns,before.st_mtime_ns);self.assertEqual(cow.creation_time(target),birth);self.assertEqual(subprocess.check_output(['xattr','-lx',str(target)]),attrs)
   self.assertNotEqual(source.stat().st_ino,target.stat().st_ino);target.write_bytes(b'edited');self.assertEqual(source.read_bytes(),content)
   self.assertEqual(subprocess.check_output(['ls','-le',str(target)]).splitlines()[1:],acl)
 def test_changed_target_is_preserved(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();source=root/'source.json';target=root/'target.json';source.write_text('source');target.write_text('unique')
   with self.assertRaises(ValueError):cow.clone_replace(source,target,cow.digest(source),root)
   self.assertEqual(target.read_text(),'unique')
 def test_hardlinks_symlinks_and_database_files_are_rejected(self):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();source=root/'source.json';source.write_text('source');link=root/'link.json';link.symlink_to(source);hard=root/'hard.json';os.link(source,hard);database=root/'state.sqlite';database.write_text('database')
   for p in [source,link,hard,database]:self.assertFalse(cow.eligible(p,root,0))
if __name__=='__main__':unittest.main()
