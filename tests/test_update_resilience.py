"""Exercise real transaction boundaries with injected source failures."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('resilient_update', Path(__file__).resolve().parents[1]/'scripts/update.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

class Resilience(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(u, 'DIST', self.root)
        self.patch.start()
        self.budget = patch.object(u, 'DEADLINE', None)
        self.budget.start()
        self.sleep = patch.object(u.time, 'sleep')
        self.sleep.start()
        self.data = {'races':[{'id':'jra-r','date':u.DATE,'status':'entries','field':1}],
                     'horses':[{'id':'jra-h','raceId':'jra-r','number':'1','historyComplete':True,
                                'history':[],'mark':'◎','note':'saved'}]}
        self.write_data(self.data)
        (self.root/'jra-odds.json').write_text('{"odds":{},"meta":{}}')
        (self.root/'jra-forecasts.json').write_text(json.dumps({'jra-r':{'forecastKind':'pre-race','status':'predicted',
            'items':{'jra-h':{'evidence':{'recent':[]}}}}}))

    def tearDown(self):
        self.sleep.stop(); self.budget.stop(); self.patch.stop(); self.tmp.cleanup()

    def write_data(self, data):
        (self.root/'results-data.js').write_text('const RESULT_DATA='+json.dumps(data)+';\n')

    def test_retry_restores_partial_writes_before_second_attempt(self):
        original = u.snapshot()
        def source(*args):
            self.assertEqual(u.snapshot(), original)
            if source.calls == 0:
                source.calls += 1
                (self.root/'results-data.js').write_text('broken')
                raise OSError('temporary connection error')
        source.calls = 0
        with patch.object(u, 'run', side_effect=source) as run:
            result = u.stage('entries', (), lambda: u.verify(self.root/'results-data.js'))
        self.assertTrue(result['success']); self.assertEqual(result['attempts'], 2)
        self.assertEqual(run.call_count, 2); self.assertEqual(u.snapshot(), original)

    def test_forecast_crash_does_not_discard_valid_entries_or_block_odds(self):
        called = []
        def source(script, *args):
            called.append(script)
            if script.endswith('import-jra-entries.py'):
                data = u.parse(self.root/'results-data.js'); data['horses'][0]['jockey']='changed official jockey'
                self.write_data(data)
            elif script.endswith('forecast-current-jra.py'):
                (self.root/'jra-forecasts.json').write_text('invalid partial write')
                raise subprocess.CalledProcessError(1, ['forecast'])
            elif script.endswith('import-jra-odds.py'):
                (self.root/'jra-odds.json').write_text('{"odds":{"jra-r":{"tan":{"1":[2.5]}}},"meta":{}}')
        with patch.object(u, 'run', side_effect=source):
            result = u.update('jra')
        self.assertTrue(result['success']); self.assertTrue(result['partial'])
        self.assertEqual([x['success'] for x in result['stages']], [True,True,False,True,True])
        self.assertEqual(u.parse(self.root/'results-data.js')['horses'][0]['jockey'], 'changed official jockey')
        self.assertEqual(u.parse(self.root/'results-data.js')['horses'][0]['note'], 'saved')
        self.assertIn('scripts/import-jra-odds.py', called)
        self.assertEqual(json.loads((self.root/'jra-odds.json').read_text())['odds']['jra-r']['tan']['1'],[2.5])
        self.assertIn('jra-r', json.loads((self.root/'jra-forecasts.json').read_text()))

    def test_invalid_entries_and_odds_are_rolled_back(self):
        original = u.snapshot()
        def source(script, *args):
            if script.endswith('import-jra-entries.py'):
                data=u.parse(self.root/'results-data.js'); data['horses']=[]; self.write_data(data)
            elif script.endswith('import-jra-odds.py'):
                (self.root/'jra-odds.json').write_text('{"odds":{"unknown-race":{}},"meta":{}}')
        with patch.object(u, 'run', side_effect=source):
            result = u.update('jra')
        self.assertTrue(result['partial']); self.assertEqual(u.snapshot(), original)
        self.assertFalse(result['stages'][0]['success']); self.assertFalse(result['stages'][-1]['success'])

    def test_expired_budget_never_starts_a_subprocess(self):
        original = u.snapshot()
        with patch.object(u,'DEADLINE',time.monotonic()-1), patch.object(u.subprocess,'run') as run:
            result=u.stage('odds',('fake.py',),lambda:None)
        self.assertFalse(result['success']); run.assert_not_called()
        self.assertEqual(result['attempts'],1); self.assertEqual(u.snapshot(),original)

    def test_partial_run_publishes_valid_feed_but_reports_failure(self):
        scripts=self.root/'scripts'; scripts.mkdir()
        original=Path(__file__).resolve().parents[1]/'scripts/public-racecards.py'
        (scripts/'public-racecards.py').write_bytes(original.read_bytes())
        def source(script,*args):
            if script.endswith('forecast-current-jra.py'): raise OSError('forecast source failed')
            if script.endswith('import-jra-entries.py'):
                data=u.parse(self.root/'results-data.js'); data['horses'][0]['load']='56'
                self.write_data(data)
        with patch.object(u,'ROOT',self.root), patch.object(u,'has_meeting',return_value=True), patch.object(u,'run',side_effect=source):
            with self.assertRaises(SystemExit) as error: u.main()
        self.assertEqual(error.exception.code,1)
        feed=json.loads((self.root/'feed.json').read_text())
        self.assertTrue(feed['results'][0]['partial'])
        self.assertTrue(feed['results'][0]['success'])
        self.assertEqual(feed['jra']['horses'][0]['load'],'56')
        self.assertTrue((self.root/'public-racecards.json').exists())
        report=json.loads((self.root/'update-report.json').read_text())
        self.assertFalse(report['results'][0]['stages'][2]['success'])

    def test_changed_runner_set_is_not_published_as_current_forecast(self):
        forecasts={'jra-r':{'items':{'old-horse':{}}},'unknown-race':{'items':{}}}
        self.assertEqual(u.feed_forecasts(self.data,forecasts),{})

    def test_all_failed_stages_keep_the_previous_snapshot(self):
        original=u.snapshot()
        with patch.object(u,'run',side_effect=OSError('source unavailable')):
            result=u.update('jra')
        self.assertFalse(result['success']); self.assertTrue(result['partial'])
        self.assertEqual(u.snapshot(),original)

if __name__=='__main__': unittest.main()
