import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("blind", Path(__file__).resolve().parents[1] / "scripts/blind-backtest.py")
blind = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blind)

def sample():
    return {"race_id":"race-1", "start_at":"2026-10-10T15:00:00+09:00",
            "forecast_created_at":"2026-10-10T14:00:00+09:00",
            "forecast_kind":"pre-race",
            "prediction":{"a":{"win_probability":0.7},"b":{"win_probability":0.3}},
            "result":{"winner_id":"a","runner_ids":["a","b"]}}

class BlindTests(unittest.TestCase):
    def test_scores_saved_forecast(self):
        r = blind.blind_evaluate([sample()])
        self.assertEqual(r["evaluated"],1)
        self.assertEqual(r["top1_accuracy"],1)
        self.assertAlmostEqual(r["mean_multiclass_brier"],0.18)

    def test_rejects_retrospective(self):
        r = sample();r["forecast_kind"]="retrospective"
        self.assertEqual(blind.blind_evaluate([r])["evaluated"],0)

    def test_rejects_post_start(self):
        r=sample();r["forecast_created_at"]=r["start_at"]
        self.assertEqual(blind.blind_evaluate([r])["evaluated"],0)

    def test_predictor_never_receives_result(self):
        r=sample()
        def predict(x):
            self.assertNotIn("result",x)
            self.assertNotIn("prediction",x)
            return r["prediction"]
        self.assertEqual(blind.blind_evaluate([r],predictor=predict)["evaluated"],1)

    def test_rejects_inconsistent_probability(self):
        r=sample();r["prediction"]["a"]["win_probability"]=0.8
        self.assertEqual(blind.blind_evaluate([r])["evaluated"],0)

    def test_rejects_changed_field(self):
        r=sample();r["result"]["runner_ids"]=["a","c"]
        self.assertEqual(blind.blind_evaluate([r])["evaluated"],0)

if __name__=="__main__":
    unittest.main()
