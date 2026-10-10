import datetime
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("county_plan", ROOT / "agent-loops/tracelearn-tutor-countywide/prepare-plan.py")
PLAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAN)


class CountywideTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.query = {"engine": "organic", "keyword": "private tutor Bucks", "coordinate": "51.6,-0.7,200", "depth": 20, "sourceKind": "own_site"}
        self.new = dict(self.query, keyword="primary teacher Winslow")

    def history(self, status, **fields):
        return {"runId": "old", "deadlineAt": (self.now - datetime.timedelta(hours=1)).isoformat(),
                "intents": [dict(query=self.query, status=status, maximumCents=100, **fields)]}

    def test_submitted_searches_are_cached_despite_changed_depth_and_labels(self):
        for status in ["failed", "succeeded", "uncertain", "reserved", "submitting"]:
            fields = {"costCents": 1, "costUsd": 0.0035} if status in ["failed", "succeeded"] else {}
            changed = dict(self.query, keyword="PRIVATE  tutor Bucks", depth=10, sourceKind="school_parent", town="Different label")
            result, manifest = PLAN.prepare([changed, self.new], [self.history(status, **fields)], self.now)
            self.assertEqual(result, [self.new])
            self.assertEqual(manifest["excludedSearches"], 1)
            self.assertEqual(manifest["priorExposureCents"], 1 if fields else 100)

    def test_unsubmitted_queries_remain_usable_without_double_counting_history(self):
        history = self.history("planned")
        result, manifest = PLAN.prepare([self.query, self.query, self.new], [history, history], self.now)
        self.assertEqual(result, [self.query, self.new])
        self.assertEqual(manifest["priorExposureCents"], 0)

    def test_shared_budget_reduces_new_allowance_and_refuses_exhaustion(self):
        history = self.history("succeeded", costCents=9500, costUsd=95.0)
        _, manifest = PLAN.prepare([self.new], [history], self.now)
        self.assertEqual(manifest["budgetCents"], 500)
        history["intents"][0].update(costCents=9950, costUsd=99.5)
        with self.assertRaisesRegex(ValueError, "ceiling"):
            PLAN.prepare([self.new], [history], self.now)

    def test_live_previous_window_and_unverifiable_cost_fail_closed(self):
        history = self.history("succeeded", costCents=1, costUsd=0.0035)
        history["deadlineAt"] = (self.now + datetime.timedelta(hours=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "active"):
            PLAN.prepare([self.new], [history], self.now)
        history["deadlineAt"] = (self.now - datetime.timedelta(hours=1)).isoformat()
        history["intents"][0]["costUsd"] = 10.0
        with self.assertRaisesRegex(ValueError, "receipt ledger"):
            PLAN.prepare([self.new], [history], self.now)


if __name__ == "__main__":
    unittest.main()
