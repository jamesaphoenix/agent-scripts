import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("drift", Path(__file__).resolve().parents[1] / "scripts/cross-repo-drift.py")
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)


class CrossRepoDriftTests(unittest.TestCase):
    def test_parse_list_skips_comments_and_marks_comment_only_entries(self):
        entries = drift.parse_list("# header\n\na.ts\nb.sh  # comments\n")
        self.assertEqual(entries, [("a.ts", False), ("b.sh", True)])

    def test_identical_files_report_nothing(self):
        files = {"a.ts": "export const x = 1\n"}
        self.assertEqual(drift.compare([("a.ts", False)], files.get, files.get), [])

    def test_any_byte_difference_is_drift_for_exact_entries(self):
        a, b = {"a.ts": "const x = 1 // old\n"}, {"a.ts": "const x = 1 // new\n"}
        self.assertEqual(drift.compare([("a.ts", False)], a.get, b.get)[0]["status"], "drifted")

    def test_comment_only_entries_ignore_comment_wording_but_not_code(self):
        a = {"a.ts": "/* one — two */\nconst x = 1\n", "b.sh": "# note\necho hi\n"}
        b = {"a.ts": "/* one - two */\nconst x = 1\n", "b.sh": "# changed\necho bye\n"}
        findings = drift.compare([("a.ts", True), ("b.sh", True)], a.get, b.get)
        self.assertEqual([f["path"] for f in findings], ["b.sh"])

    def test_missing_side_is_reported(self):
        findings = drift.compare([("gone.ts", False)], {"gone.ts": "x"}.get, {}.get)
        self.assertEqual(findings, [{"path": "gone.ts", "status": "missing", "missing_in": ["octospark"]}])


if __name__ == "__main__":
    unittest.main()
