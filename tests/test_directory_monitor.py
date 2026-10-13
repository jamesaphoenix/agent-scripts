import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[1] / "agent-loops/tutor-lead-pathways"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("directory_monitor", ROOT / "monitor_directory_profiles.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_a_stage_ending_short_of_the_goal_does_not_reach_the_target(self):
        status = {"observedAt": "2026-10-11T20:00:00+00:00", "phase": "stage_complete", "bucksPrivateProfiles": 57}
        value = monitor.health(status, {"deadlineUnix": 2000000000, "target": 1000}, False, 1791748860)
        self.assertTrue(value["stageTerminal"])
        self.assertFalse(value["targetReached"])

    def test_live_progress_and_stale_progress_are_distinct(self):
        status = {"observedAt": "2026-10-11T20:00:00+00:00", "phase": "running", "bucksPrivateProfiles": 57}
        job = {"deadlineUnix": 2000000000, "target": 1000}
        self.assertEqual(monitor.health(status, job, True, 1791748860)["health"], "healthy")
        self.assertEqual(monitor.health(status, job, True, 1791749500)["health"], "degraded")
        self.assertEqual(monitor.health(status, job, False, 1791748860)["health"], "degraded")
