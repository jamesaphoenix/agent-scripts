import importlib.util
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cache_hygiene',ROOT/'agent-loops/cache-hygiene/maintenance.py')
hygiene=importlib.util.module_from_spec(spec);spec.loader.exec_module(hygiene)

class HygieneTests(unittest.TestCase):
    def test_owned_old_direct_child_is_eligible(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name);child=parent/'cache';child.mkdir()
            self.assertTrue(hygiene.cache_candidate(child,parent,[],time.time()+1))

    def test_path_escape_and_symlink_are_never_eligible(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name);child=parent/'source';child.mkdir();link=parent/'cache';link.symlink_to(child)
            self.assertFalse(hygiene.cache_candidate(link,parent,[],time.time()+1))
            self.assertFalse(hygiene.cache_candidate(child,parent/'other',[],time.time()+1))

    def test_open_descendant_retains_entire_cache(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name);child=parent/'cache';child.mkdir()
            handle=str(child/'weights.bin').removeprefix('/private').casefold()
            self.assertFalse(hygiene.cache_candidate(child,parent,[handle],time.time()+1))

    def test_prefix_collision_is_not_an_open_descendant(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name);child=parent/'cache';child.mkdir()
            handle=str(parent/'cache-other/file').removeprefix('/private').casefold()
            self.assertTrue(hygiene.cache_candidate(child,parent,[handle],time.time()+1))

    def test_recent_directory_is_retained(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name);child=parent/'cache';child.mkdir()
            self.assertFalse(hygiene.cache_candidate(child,parent,[],time.time()-3600))

    def test_hidden_container_writer_blocks_readiness_cleanup(self):
        self.assertTrue(hygiene.readiness_busy([],True))
        self.assertTrue(hygiene.readiness_busy(['node audit/mobile-readiness/run.ts'],False))
        self.assertFalse(hygiene.readiness_busy(['python3 maintenance.py'],False))

    def test_mount_of_ancestor_protects_worktree(self):
        tree=Path('/Users/test/repo/.worktrees/branch')
        self.assertTrue(hygiene.busy_tree(tree,[],[],[tree.parent]))

    def test_dependency_walk_never_follows_external_symlinks_or_enters_unity_state(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);repo=root/'repo';repo.mkdir();(repo/'.git').mkdir()
            deps=repo/'node_modules';deps.mkdir();(deps/'nested').mkdir()
            unity=repo/'Library';unity.mkdir();(unity/'node_modules').mkdir()
            external=root/'external';external.mkdir();(external/'node_modules').mkdir()
            (repo/'linked').symlink_to(external)
            app=repo/'app';app.mkdir();(app/'node_modules').mkdir()
            self.assertEqual(set(hygiene.project_dependency_roots(repo)),{deps,app/'node_modules'})
            self.assertEqual(hygiene.git_root(app,root),repo)
            self.assertIsNone(hygiene.git_root(external,root))

    def test_storage_flag_supports_both_cli_generations(self):
        helper=ROOT/'agent-loops/docker-cleanup/lib/cache-storage-flag.sh'
        for expected in ('--max-used-space','--keep-storage'):
            # Use an executable fixture to test the whole command-prefix contract.
            with tempfile.TemporaryDirectory() as name:
                cli=Path(name)/'docker';cli.write_text('#!/bin/sh\nprintf "%s\\n" "'+expected+'"\n');cli.chmod(0o700)
                p=subprocess.run(['bash','-c','source "$1"; builder_storage_flag "$2"','test',str(helper),str(cli)],capture_output=True,text=True)
                self.assertEqual((p.returncode,p.stdout.strip()),(0,expected))

    def test_discovery_timeout_kills_only_its_worker_and_reports_incomplete_scan(self):
        worker=mock.Mock()
        worker.communicate.side_effect=[subprocess.TimeoutExpired('scanner',0.01),('', 'stalled filesystem')]
        with tempfile.TemporaryDirectory() as name, mock.patch.object(hygiene.subprocess,'Popen',return_value=worker):
            paths,report=hygiene.bounded_dependency_discovery(Path(name)/'projects',Path(name),timeout=0.01)
        worker.kill.assert_called_once()
        self.assertEqual(paths,[])
        self.assertEqual(report['status'],'timed_out')

    def test_unknown_storage_flag_fails_closed(self):
        helper=ROOT/'agent-loops/docker-cleanup/lib/cache-storage-flag.sh'
        with tempfile.TemporaryDirectory() as name:
            cli=Path(name)/'docker';cli.write_text('#!/bin/sh\necho unsupported\n');cli.chmod(0o700)
            p=subprocess.run(['bash','-c','source "$1"; builder_storage_flag "$2"','test',str(helper),str(cli)],capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)

if __name__=='__main__':unittest.main()
