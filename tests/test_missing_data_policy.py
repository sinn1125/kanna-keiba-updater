import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("missing_data_policy", ROOT / "scripts/missing_data_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

NOW = "2026-10-31T10:00:00+09:00"
GOOD = {"status": "confirmed", "checked_at": NOW}


class MissingDataPolicyTests(unittest.TestCase):
    def records(self):
        return {k: dict(GOOD) for k in ("entries", "withdrawals", "odds", "history", "track")}

    def test_workout_absent_keeps_prediction(self):
        result = policy.decision(self.records(), ["a", "b"], {}, NOW)
        self.assertTrue(result["prediction_allowed"])
        self.assertTrue(result["prediction_provisional"])
        self.assertEqual(result["workout_model"], "missing_workout_baseline")
        self.assertEqual(result["workout_coverage"]["status"], "absent")

    def test_partial_workouts_not_zero(self):
        w = {"a": {"status": "confirmed", "sessions": [{"time": "55.0"}]}}
        result = policy.decision(self.records(), ["a", "b"], w, NOW)
        self.assertEqual(result["workout_coverage"]["status"], "partial")
        self.assertEqual(result["workout_coverage"]["available"], 1)
        self.assertEqual(result["workout_model"], "missing_workout_baseline")

    def test_full_workouts(self):
        w = {k: {"status": "confirmed", "sessions": [1]} for k in ("a", "b")}
        result = policy.decision(self.records(), ["a", "b"], w, NOW)
        self.assertEqual(result["workout_coverage"]["status"], "full")

    def test_missing_odds_blocks_bet_not_prediction(self):
        r = self.records()
        r["odds"] = {"status": "fetch_failed"}
        result = policy.decision(r, ["a"], {}, NOW)
        self.assertTrue(result["prediction_allowed"])
        self.assertFalse(result["bet_recommendation_allowed"])
        self.assertFalse(result["expected_value_allowed"])
        self.assertIn("odds", result["reason"])

    def test_stale_withdrawals_block_bets(self):
        r = self.records()
        r["withdrawals"]["checked_at"] = "2026-10-31T09:00:00+09:00"
        result = policy.decision(r, ["a"], {}, NOW)
        self.assertEqual(result["data_states"]["withdrawals"], "stale")
        self.assertFalse(result["bet_recommendation_allowed"])

    def test_unverified_entries_do_not_make_new_prediction(self):
        r = self.records()
        r["entries"] = {"status": "fetch_failed"}
        self.assertFalse(policy.decision(r, ["a"], {}, NOW)["prediction_allowed"])

    def test_invalid_and_future_timestamps_fail_closed(self):
        self.assertEqual(policy.classify_record({"status": "confirmed"}, NOW), "invalid")
        self.assertEqual(policy.classify_record({"status": "confirmed", "checked_at": "2026-10-31T11:00:00+09:00"}, NOW), "invalid")
        self.assertEqual(policy.classify_record({"status": "confirmed", "checked_at": "2026-10-31T10:00:00"}, NOW), "invalid")


if __name__ == "__main__":
    unittest.main()
