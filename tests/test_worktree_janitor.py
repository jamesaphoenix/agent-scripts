import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("janitor", Path(__file__).resolve().parents[1] / "agent-loops/worktree-janitor/janitor.py")
janitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(janitor)

class JanitorTests(unittest.TestCase):
    def test_old_merged_pr_does_not_allow_removing_reused_unmerged_branch(self):
        def git(repo, *args, **kwargs):
            if args[0] == "rev-parse" and "--verify" in args: return 0, "target"
            if args[0] == "merge-base": return 1, ""
            if args[0] == "merge-tree": return 0, "tree-with-new-work"
            if args[0] == "rev-parse": return 0, "target-tree"
            raise AssertionError(args)
        with patch.object(janitor, "git", side_effect=git):
            self.assertEqual(janitor.merged_signals(Path("."), "reused", ["main"], {"reused": "PR #1 merged"}), [])

    def test_current_ancestor_is_still_recognized(self):
        with patch.object(janitor, "git", return_value=(0, "target")):
            self.assertEqual(janitor.merged_signals(Path("."), "merged", ["main"], {}), ["ancestor of main"])

if __name__ == "__main__": unittest.main()
