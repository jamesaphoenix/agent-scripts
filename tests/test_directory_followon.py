import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

PATH = Path(__file__).parents[1] / "agent-loops/tutor-lead-pathways/prepare_profile_followon.py"
sys.path.insert(0, str(PATH.parent))
spec = importlib.util.spec_from_file_location("followon", PATH)
followon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(followon)


class FollowOnTests(unittest.TestCase):
    def fixture(self, root):
        parent = root / "parent"
        parent.mkdir()
        job = {"startedAt": "2026-10-11T20:00:00+00:00", "deadlineUnix": 1791750000, "pageLimit": 5000, "target": 1000}
        state = {"phase": "stage_complete", "attempts": 7, "records": [], "queue": ["https://www.findtutors.co.uk/tutors-milton-keynes/"], "seen": [], "errors": []}
        for name, value in [("job.json", job), ("state.json", state), ("status.json", {"phase": "stage_complete"})]:
            (parent / name).write_text(json.dumps(value))
        (parent / "receipts").mkdir()
        (parent / "pages").mkdir()
        return parent

    def test_followon_preserves_parent_and_original_attempts_with_a_distinct_bounded_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = self.fixture(root)
            before = (parent / "job.json").read_bytes(), (parent / "state.json").read_bytes()
            result = followon.prepare(parent, root / "child", 2, 30, 1200)
            job = json.loads((root / "child/job.json").read_text())
            self.assertEqual(job["pageLimit"], 37)
            self.assertEqual(job["initialAttemptCount"], 7)
            self.assertEqual(job["newAttemptLimit"], 30)
            self.assertEqual(job["paidBudget"], 0)
            self.assertEqual(job["target"], 1200)
            self.assertEqual(before, ((parent / "job.json").read_bytes(), (parent / "state.json").read_bytes()))
            self.assertEqual(result["phase"], "prepared")
            child_before = (root / "child/job.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "already exists"):
                followon.prepare(parent, root / "child", 2, 30, 1200)
            self.assertEqual(child_before, (root / "child/job.json").read_bytes())

    def test_live_parents_and_changed_cache_receipts_cannot_start_a_followon(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = self.fixture(root)
            status = parent / "status.json"
            status.write_text(json.dumps({"phase": "running"}))
            with self.assertRaisesRegex(ValueError, "terminal"):
                followon.prepare(parent, root / "child", 2, 30, 1200)
            status.write_text(json.dumps({"phase": "stage_complete"}))
            url = "https://www.findtutors.co.uk/buckinghamshire-tutors/"
            key = hashlib.sha256(url.encode()).hexdigest()
            (parent / "receipts" / (key + ".json")).write_text(json.dumps({"url": url, "sha256": "wrong"}))
            (parent / "pages" / (key + ".html")).write_bytes(b"body")
            with self.assertRaisesRegex(ValueError, "hash"):
                followon.prepare(parent, root / "child", 2, 30, 1200)
            self.assertFalse((root / "child").exists())

