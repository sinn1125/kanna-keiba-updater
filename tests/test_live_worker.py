import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('worker',Path(__file__).resolve().parents[1]/'scripts/run-live-worker.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)

class LiveWorkerTests(unittest.TestCase):
    def test_japan_time_and_final_result_grace(self):
        self.assertTrue(w.live_window(datetime(2026,10,24,0,0,tzinfo=timezone.utc)))
        self.assertTrue(w.live_window(datetime(2026,10,24,8,10,tzinfo=timezone.utc)))
        self.assertFalse(w.live_window(datetime(2026,10,24,8,11,tzinfo=timezone.utc)))
        self.assertFalse(w.live_window(datetime(2026,10,23,23,59,tzinfo=timezone.utc)))
    def test_retry_backoff_is_bounded(self):
        self.assertEqual(w.retry_delay(120,0),120)
        self.assertEqual(w.retry_delay(120,1),240)
        self.assertEqual(w.retry_delay(120,100),900)

if __name__=='__main__':unittest.main()
