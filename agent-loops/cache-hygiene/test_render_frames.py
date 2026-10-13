import importlib.util,os,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('frames',Path(__file__).with_name('render_frames.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class FrameRetention(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.repo=self.base/'just-understanding-data';self.repo.mkdir();subprocess.run(['git','init','-q',str(self.repo)],check=True)
  self.root=self.repo/'artifacts/claude-code';self.folder=self.root/'series-v1/episodes/demo/renders/overlay-final';self.folder.mkdir(parents=True);self.png=self.folder/'frame-0001.png';self.png.write_bytes(b'render output');self.state=self.base/'state'
 def tearDown(self):self.temp.cleanup()
 def run_cleanup(self,**kwargs):return m.run([self.root],self.state,apply=True,age_days=0,quiet_seconds=0,snapshot_fn=lambda:([],[],[],False),video_fn=lambda p:True,**kwargs)
 def test_generated_only_and_native_contract_preserved(self):
  source=self.folder/'poster.png';source.write_bytes(b'original');native=self.root/'series-v1/episodes/demo/native/final-frames/frame-0001.png';native.parent.mkdir(parents=True);native.write_bytes(b'verification frame');movie=self.folder.parent/'final.mp4';movie.write_bytes(b'encoded movie')
  self.assertEqual(self.run_cleanup()['removed_files'],1);self.assertEqual(source.read_bytes(),b'original');self.assertEqual(native.read_bytes(),b'verification frame');self.assertEqual(movie.read_bytes(),b'encoded movie')
 def test_live_handle_retained(self):
  r=m.run([self.root],self.state,True,0,0,snapshot_fn=lambda:([str(self.png).casefold()],[],[],False),video_fn=lambda p:True);self.assertTrue(self.png.exists());self.assertEqual(r['results'],{'retained_open_handle':1})
 def test_relative_render_process_retained(self):
  r=m.run([self.root],self.state,True,0,0,snapshot_fn=lambda:([str(self.root).casefold()],['ffmpeg -i input.mp4 output.mp4'],[],False),video_fn=lambda p:True);self.assertTrue(self.png.exists());self.assertEqual(r['results'],{'retained_render_process':1})
 def test_invalid_final_retained(self):
  r=m.run([self.root],self.state,True,0,0,snapshot_fn=lambda:([],[],[],False),video_fn=lambda p:False);self.assertTrue(self.png.exists());self.assertEqual(r['results'],{'retained_no_verified_final':1})
 def test_tracked_png_retained(self):
  subprocess.run(['git','-C',str(self.repo),'add',str(self.png)],check=True);self.assertEqual(self.run_cleanup()['results'],{'retained_tracked_source':1});self.assertTrue(self.png.exists())
 def test_fresh_frames_retained_by_default_age(self):
  r=m.run([self.root],self.state,True,7,0,snapshot_fn=lambda:([],[],[],False),video_fn=lambda p:True);self.assertEqual(r['removed_files'],0);self.assertTrue(self.png.exists())
 def test_symlink_root_rejected(self):
  link=self.base/'artifacts/claude-code';link.parent.mkdir();link.symlink_to(self.root,target_is_directory=True)
  with self.assertRaises(ValueError):m.run([link],self.state,True,0,0)
  self.assertTrue(self.png.exists())
 def test_changed_frame_not_unlinked_or_counted(self):
  def snapshot():self.png.write_bytes(b'new render');return [],[],[],False
  r=m.run([self.root],self.state,True,0,0,snapshot_fn=snapshot,video_fn=lambda p:True);self.assertEqual(r['removed_files'],0);self.assertEqual(r['removed_directory_allocation_bytes'],0);self.assertEqual(self.png.read_bytes(),b'new render')
 def test_registered_scope_case_alias_requires_filesystem_identity(self):
  scope=Path('/Users/demo/Desktop/projects');tree=Path('/Users/demo/desktop/projects/trace-learn/.worktrees/example')
  with patch.object(m.os.path,'samefile',return_value=True):self.assertEqual(m.scoped_checkout(tree,scope),scope/'trace-learn/.worktrees/example')
  with patch.object(m.os.path,'samefile',return_value=False):self.assertIsNone(m.scoped_checkout(tree,scope))
  self.assertIsNone(m.scoped_checkout(Path('/Users/demo/Desktop/projects-other/trace-learn'),scope))
 def test_discovery_finds_primary_render_root(self):
  self.assertEqual(m.roots_for_projects(self.base),[self.root])
if __name__=='__main__':unittest.main()
