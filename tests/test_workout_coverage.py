import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("coverage", Path(__file__).resolve().parents[1] / "scripts/workout-coverage.py")
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)

class CoverageTests(unittest.TestCase):
    def test_unrecorded_is_not_no_workout(self):
        self.assertEqual(coverage.workout_status(['a', 'b'], {})['coverage'], 'unknown')
    def test_partial(self):
        r = coverage.workout_status(['a', 'b'], {'a': {'status': 'confirmed', 'sessions': [1]}})
        self.assertEqual(r['coverage'], 'partial')
        self.assertEqual(r['horses']['b'], 'unrecorded')
    def test_fallback_does_not_overwrite_scores(self):
        data = {'a': {'winScore': None}, 'b': {'winScore': 0.4}}
        out, status = coverage.fallback_scores(data, ['a', 'b'])
        self.assertEqual(out['a']['winScore'], 0.5)
        self.assertEqual(out['b']['winScore'], 0.4)
        self.assertIsNone(data['a']['winScore'])
        self.assertEqual(status['mode'], 'provisional')
    def test_all_missing_does_not_claim_trained_model(self):
        data = {'a': {'winScore': None}, 'b': {'winScore': None}}
        out, status = coverage.fallback_scores(data, ['a', 'b'])
        self.assertEqual(status['missing'], 2)
        self.assertTrue(all(v['scoreOrigin'] == 'untrained_uniform_prior' for v in out.values()))
    def test_original_scores_untouched(self):
        data = {'a': {'winScore': 0.6}, 'b': {'winScore': 0.4}}
        out, status = coverage.fallback_scores(data, ['a', 'b'])
        self.assertEqual(out, data)
        self.assertEqual(status['mode'], 'original')

if __name__ == '__main__': unittest.main()
