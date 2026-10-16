import importlib.util
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1] / "agent-loops/tutor-lead-pathways"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("directory_monitor", ROOT / "monitor_directory_profiles.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_terminal_cleanup_removes_only_scratch_and_retains_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor.save(root / 'job.json', {'deadlineUnix': 2000000000, 'target': 5000})
            monitor.save(root / 'status.json', {'phase': 'stage_complete', 'observedAt': monitor.now(), 'pid': 123})
            (root / 'state.json.tmp').write_text('interrupted write')
            (root / 'profiles.json').write_text('retained evidence')
            (root / 'status.json.tmp').symlink_to(root / 'profiles.json')
            with patch.object(monitor.subprocess, 'run', side_effect=[
                    subprocess.CompletedProcess(['ps'], 1, '', ''),
                    subprocess.CompletedProcess(['launchctl'], 0, '', '')]) as run:
                monitor.watch(root, 'com.jud.profile-test', 'com.jud.profile-test-watch')
            self.assertEqual(run.call_args_list[-1].args[0][1], 'bootout')
            self.assertFalse((root / 'state.json.tmp').exists())
            self.assertTrue((root / 'status.json.tmp').is_symlink())
            self.assertEqual((root / 'profiles.json').read_text(), 'retained evidence')
            report = json.loads((root / 'cleanup.json').read_text())
            self.assertEqual(report['removed'], [str(root / 'state.json.tmp')])
            self.assertIn(str(root / 'pages'), report['retained'])

    def test_terminal_cleanup_waits_for_the_owner_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'state.json.tmp').write_text('active write')
            with (root / 'owner.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertIsNone(monitor.cleanup(root))
            self.assertTrue((root / 'state.json.tmp').exists())
            self.assertFalse((root / 'cleanup.json').exists())

    def test_recovery_timeout_is_recorded_without_claiming_a_restart_succeeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor.save(root / 'job.json', {'deadlineUnix': 2000000000, 'target': 5000})
            monitor.save(root / 'status.json', {'phase': 'prepared', 'observedAt': monitor.now(), 'pid': 0})
            with patch.object(monitor.subprocess, 'run', side_effect=[
                    subprocess.CompletedProcess(['ps'], 1, '', ''),
                    subprocess.TimeoutExpired(['launchctl'], 10)]):
                monitor.watch(root, 'com.jud.profile-test', 'com.jud.profile-test-watch')
            report = json.loads((root / 'monitor/latest.json').read_text())
            self.assertEqual(report['action'], 'restart_status_unconfirmed')
            self.assertEqual(report['recoveries'], 1)
            self.assertEqual(report['paidCostUsd'], 0)

    def test_process_observation_timeout_cannot_authorise_an_owner_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor.save(root / 'job.json', {'deadlineUnix': 2000000000, 'target': 5000})
            monitor.save(root / 'status.json', {'phase': 'running', 'observedAt': monitor.now(), 'pid': 123})
            with patch.object(monitor.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['ps'], 10)) as run:
                monitor.watch(root, 'com.jud.profile-test', 'com.jud.profile-test-watch')
            run.assert_called_once()
            report = json.loads((root / 'monitor/latest.json').read_text())
            self.assertIsNone(report['processLive'])
            self.assertEqual(report['action'], 'none')
            self.assertEqual(report['recoveries'], 0)

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
