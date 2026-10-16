import datetime
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tutor_watch", ROOT / "agent-loops/tracelearn-tutor-watch/watch.py")
WATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCH)
UTC = datetime.timezone.utc


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 10, 10, 22, tzinfo=UTC)
        self.job = {"runId": "bucks-test", "deadline": (self.now + datetime.timedelta(hours=8)).isoformat(),
                    "budgetCents": 2000, "priorExposureCents": 89}
        self.snapshot = {"runId": "bucks-test", "status": "running", "stopReason": "", "paidStopReason": "",
                         "processLive": True, "updatedAt": self.now.isoformat(), "deadlineAt": self.job["deadline"],
                         "nextQueryAt": (self.now - datetime.timedelta(minutes=1)).isoformat(), "pages": 40,
                         "sites": 20, "sources": 30, "candidates": 7, "frontier": 2, "pendingSeeds": 0,
                         "maxPages": 1000, "maxSites": 500, "queries": {"succeeded": 5, "planned": 50},
                         "budget": {"spentCents": 6, "exposureCents": 6, "reservedCents": 0, "uncertainCents": 0,
                                    "remainingCents": 1994}}
        self.previous = {"progress": WATCH.progress(self.snapshot), "lastProgressAt": self.now.isoformat(), "recoveries": 0}

    def assess(self):
        return WATCH.assess(self.snapshot, self.job, self.previous, self.now)

    def stale(self):
        earlier = (self.now - datetime.timedelta(minutes=35)).isoformat()
        self.snapshot["updatedAt"] = earlier
        self.previous["lastProgressAt"] = earlier

    def test_real_progress_is_healthy_and_a_heartbeat_is_not_progress(self):
        self.assertEqual(self.assess()["health"], "healthy")
        self.previous["lastProgressAt"] = (self.now - datetime.timedelta(minutes=35)).isoformat()
        self.assertEqual(self.assess()["action"], "restart")
        self.snapshot["pages"] += 1
        self.assertEqual(self.assess()["health"], "healthy")

    def test_stalled_owned_process_and_missing_process_request_recovery(self):
        self.stale()
        self.assertEqual(self.assess()["action"], "restart")
        self.snapshot["processLive"] = False
        self.assertEqual(self.assess()["action"], "restart")

    def test_active_or_uncertain_paid_requests_never_restart(self):
        self.stale()
        for field in ["reservedCents", "uncertainCents"]:
            self.snapshot["budget"][field] = 100
            self.assertEqual(self.assess()["action"], "none")
            self.snapshot["budget"][field] = 0
        self.snapshot["queries"]["submitting"] = 1
        self.assertEqual(self.assess()["action"], "none")

    def test_intentional_limits_and_provider_holds_do_not_restart(self):
        self.stale()
        for reason in ["budget_limit", "page_limit", "site_limit", "provider_failure", "paid_request_uncertain"]:
            self.snapshot.update(status="stopped", stopReason=reason)
            self.assertEqual(self.assess()["action"], "none")
        self.snapshot.update(status="running", stopReason="", paidStopReason="provider_failure")
        self.assertEqual(self.assess()["action"], "none")

    def test_valid_cooldown_is_not_a_stall(self):
        self.stale()
        self.snapshot.update(frontier=0, pendingSeeds=0, nextQueryAt=(self.now + datetime.timedelta(minutes=20)).isoformat())
        self.assertEqual(self.assess()["health"], "cooldown")
        self.assertEqual(self.assess()["action"], "none")

    def test_deadline_and_budget_mismatches_fail_closed(self):
        self.stale()
        self.snapshot["deadlineAt"] = (self.now + datetime.timedelta(hours=9)).isoformat()
        self.assertEqual(self.assess()["health"], "invalid")
        self.snapshot["deadlineAt"] = self.job["deadline"]
        self.snapshot["budget"]["remainingCents"] += 1
        self.assertEqual(self.assess()["health"], "invalid")
        self.snapshot["budget"]["remainingCents"] -= 1
        self.job["priorExposureCents"] = 9900
        self.assertEqual(self.assess()["health"], "invalid")

    def test_completed_frontier_and_deadline_do_not_restart(self):
        self.snapshot.update(status="complete", stopReason="frontier_exhausted", processLive=False)
        self.assertEqual(self.assess()["health"], "finished")
        self.snapshot.update(status="running", stopReason="", processLive=True)
        self.now += datetime.timedelta(hours=9)
        self.assertEqual(self.assess()["action"], "stop")
        self.snapshot["budget"]["reservedCents"] = 100
        self.assertEqual(self.assess()["action"], "none")

    def test_recovery_cap_and_credential_startup_grace(self):
        self.stale()
        self.previous["recoveries"] = 3
        self.assertEqual(self.assess()["action"], "none")
        self.previous["recoveries"] = 0
        self.snapshot.update(processLive=False, updatedAt=self.now.isoformat())
        self.previous.update(lastProgressAt=self.now.isoformat())
        self.assertEqual(self.assess()["health"], "starting")

    def test_monitor_initialisation_keeps_original_eight_hour_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = WATCH.initialize(root, self.now)
            resumed = WATCH.initialize(root, self.now + datetime.timedelta(hours=2))
            self.assertEqual(config, resumed)
            self.assertEqual(WATCH.parse_time(config["deadline"]) - self.now, datetime.timedelta(hours=8))

    def test_process_identity_requires_exact_binary_and_checkpoint_arguments(self):
        binary, state = "/tmp/our-crawler", "/private/run/discovery.json"
        rows = f"101 {binary} discover --state {state} --resume\n102 /tmp/other discover --state {state}\n103 {binary} discover --state /other/state\n104 /bin/sh -c {binary} discover --state {state}\n"
        self.assertEqual(WATCH.owned_processes(rows, binary, state), [101])

    def test_paid_request_that_starts_between_checks_prevents_recovery(self):
        self.stale()
        self.snapshot["checkpointPaidBusy"] = True
        with mock.patch.object(WATCH, "read_snapshot", return_value=(self.job, self.snapshot)), \
                mock.patch.object(WATCH, "command") as command, mock.patch.object(WATCH.os, "kill") as kill:
            self.assertEqual(WATCH.recover(Path("/private/run"), "/tmp/our-crawler", self.previous, "restart"), "held_after_recheck")
            command.assert_not_called()
            kill.assert_not_called()

    def test_recovery_signals_only_exact_owner_and_kicks_existing_job_without_force(self):
        self.stale()
        self.snapshot["updatedAt"] = "2026-10-10T18:00:00Z"
        self.previous["lastProgressAt"] = "2026-10-10T18:00:00Z"
        self.snapshot["nextQueryAt"] = "2026-10-10T18:00:00Z"
        row = "101 /tmp/our-crawler discover --state /private/run/discovery.json --resume\n"
        responses = [SimpleNamespace(stdout=row, returncode=0), SimpleNamespace(stdout="", returncode=0),
                     SimpleNamespace(stdout=row, returncode=0), SimpleNamespace(stdout="", returncode=0),
                     SimpleNamespace(stdout="", returncode=0)]
        with mock.patch.object(WATCH, "read_snapshot", return_value=(self.job, self.snapshot)), \
                mock.patch.object(WATCH, "command", side_effect=responses) as command, mock.patch.object(WATCH.os, "kill") as kill, \
                mock.patch.object(WATCH.datetime, "datetime", wraps=datetime.datetime) as clock:
            clock.now.return_value = self.now
            self.assertEqual(WATCH.recover(Path("/private/run"), "/tmp/our-crawler", self.previous, "restart"), "restart_requested")
            kill.assert_called_once_with(101, WATCH.signal.SIGTERM)
            self.assertEqual(command.call_args_list[-1].args[0][0:2], ["/bin/launchctl", "kickstart"])
            self.assertNotIn("-k", command.call_args_list[-1].args[0])

    def test_invalid_checkpoint_still_writes_a_durable_health_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(WATCH, "read_snapshot", side_effect=ValueError("Input digest mismatch")):
                WATCH.check(Path("/private/run"), root, "/tmp/our-crawler")
            import json
            report = json.loads((root / "latest.json").read_text())
            self.assertEqual(report["health"], "invalid")
            self.assertEqual(len(list((root / "reports").glob("*.json"))), 1)

    def test_expired_monitor_saves_final_report_and_unloads_only_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            WATCH.initialize(root, datetime.datetime.now(UTC) - datetime.timedelta(hours=9))
            with mock.patch.object(WATCH, "read_snapshot", return_value=(self.job, self.snapshot)), \
                    mock.patch.object(WATCH, "command") as command, mock.patch.object(WATCH, "recover") as recover:
                WATCH.check(Path("/private/run"), root, "/tmp/our-crawler")
                recover.assert_not_called()
                self.assertEqual(command.call_args.args[0][0:2], ["/bin/launchctl", "bootout"])
                self.assertTrue(command.call_args.args[0][-1].endswith(WATCH.WATCH_LABEL))
            import json
            self.assertTrue(json.loads((root / "latest.json").read_text())["monitorExpired"])


if __name__ == "__main__":
    unittest.main()
