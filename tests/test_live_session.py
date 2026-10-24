import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('session',Path(__file__).resolve().parents[1]/'scripts/live-session.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class LiveSessionTests(unittest.TestCase):
    def test_live_window_includes_final_result_grace(self):
        self.assertTrue(s.in_window(datetime(2026,10,24,0,tzinfo=timezone.utc)))
        self.assertTrue(s.in_window(datetime(2026,10,24,8,14,tzinfo=timezone.utc)))
        self.assertFalse(s.in_window(datetime(2026,10,24,8,15,tzinfo=timezone.utc)))

    def run_clock(self, results):
        clock=[0];waits=[]
        def sleep(seconds):waits.append(seconds);clock[0]+=seconds
        with patch.object(s,'in_window',return_value=True),patch.object(s.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(s.time,'sleep',side_effect=sleep),patch.object(s,'refresh_snapshots') as refresh, \
             patch.object(s.subprocess,'run',side_effect=[SimpleNamespace(returncode=x) for x in results]) as update:
            status=s.run_session('results',5)
        self.assertEqual(refresh.call_count,3);self.assertEqual(update.call_count,3)
        self.assertEqual(waits,[120,120,60]);return status

    def test_multiple_publications_without_new_github_start(self):
        self.assertEqual(self.run_clock([0,0,0]),0)

    def test_failure_does_not_stop_following_updates(self):
        self.assertEqual(self.run_clock([1,0,0]),1)

    def test_per_component_queues_and_scheduled_only_session(self):
        w=(Path(__file__).resolve().parents[1]/'.github/workflows/update.yml').read_text()
        self.assertIn('group: official-racing-${{ matrix.component }}',w)
        self.assertIn("github.event_name == 'schedule'",w)
        self.assertIn('--minutes 30',w)
        self.assertNotIn('group: official-racing-update',w)

if __name__=='__main__':unittest.main()
