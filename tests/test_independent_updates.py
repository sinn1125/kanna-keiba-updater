import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('components',ROOT/'scripts/update-component.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class IndependentUpdates(unittest.TestCase):
    def fixture(self):
        return {'results-data.js':{'races':[{'id':'jra-r','date':c.u.DATE,'status':'entries','field':1}],
                  'horses':[{'id':'jra-h','raceId':'jra-r','number':'1','weight':'450','jockey':'original','history':[],'note':'saved'}]},
                'jra-odds.json':{'odds':{},'meta':{}},'jra-forecasts.json':{}}

    def test_disjoint_fields_merge_without_overwriting_concurrent_update(self):
        base=self.fixture()['results-data.js'];weights=copy.deepcopy(base);weights['horses'][0]['weight']='456'
        concurrent=copy.deepcopy(base);concurrent['horses'][0]['jockey']='official change'
        result=c.merge(base,weights,concurrent,'results-data.js')
        self.assertEqual(result['horses'][0]['weight'],'456');self.assertEqual(result['horses'][0]['jockey'],'official change')
        self.assertEqual(result['horses'][0]['note'],'saved')

    def test_same_field_conflict_is_not_overwritten(self):
        base=self.fixture()['results-data.js'];a=copy.deepcopy(base);b=copy.deepcopy(base)
        a['horses'][0]['weight']='456';b['horses'][0]['weight']='460'
        with self.assertRaisesRegex(ValueError,'Concurrent update conflict'):c.merge(base,a,b,'results-data.js')

    def test_entries_and_weights_have_separate_ownership(self):
        before=self.fixture()['results-data.js'];after=copy.deepcopy(before)
        after['horses'][0].update(weight='460',weightChange='+10',winOdds=2.1,jockey='new jockey')
        entries=c.restrict('entries',before,after);weights=c.restrict('weights',before,after)
        self.assertEqual(entries['horses'][0]['weight'],'450');self.assertNotIn('winOdds',entries['horses'][0])
        self.assertEqual(entries['horses'][0]['jockey'],'new jockey')
        self.assertEqual(weights['horses'][0]['weight'],'460');self.assertEqual(weights['horses'][0]['jockey'],'original')

    def test_new_ids_from_other_jobs_survive(self):
        before=self.fixture()['results-data.js'];after=copy.deepcopy(before);latest=copy.deepcopy(before)
        after['horses'][0]['weight']='456'
        latest['horses'].append({'id':'jra-other','raceId':'jra-other-r','weight':'470'})
        result=c.merge(before,after,latest,'results-data.js')
        self.assertEqual(len(result['horses']),2)

    def test_timestamp_conflict_keeps_newest(self):
        self.assertEqual(c.merge('2026-10-18T09:00:00+09:00','2026-10-18T09:10:00+09:00','2026-10-18T09:20:00+09:00','r.fetchedAt'),'2026-10-18T09:20:00+09:00')

    def test_workflow_does_not_cancel_other_components(self):
        workflow=(ROOT/'.github/workflows/update.yml').read_text()
        self.assertIn('fail-fast: false',workflow);self.assertIn('max-parallel: 4',workflow)
        self.assertIn("JRA_FETCH_WORKERS: '1'",workflow)
        for part in c.COMPONENTS:self.assertIn(part,workflow)

    def test_independent_publication_preserves_first_job_and_existing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);work=root/'work';remote=root/'remote.git';work.mkdir()
            def git(*args,cwd=work):return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True).stdout.decode().strip()
            git('init','--bare',str(remote));git('init','-b','main');git('config','user.name','test');git('config','user.email','test@example.invalid')
            (work/'dist').mkdir();(work/'scripts').mkdir()
            (work/'scripts/public-racecards.py').write_bytes((ROOT/'scripts/public-racecards.py').read_bytes())
            before=self.fixture()
            for name,data in before.items():(work/'dist'/name).write_bytes(c.encode(name,data))
            git('add','.');git('commit','-m','seed');git('remote','add','origin',str(remote));git('push','-u','origin','main')
            weights=copy.deepcopy(before);weights['results-data.js']['horses'][0]['weight']='456'
            odds=copy.deepcopy(before);odds['jra-odds.json']['odds']={'jra-r':{'単勝':{'1':[2.5]}}};c.sync_single_odds(odds['results-data.js'],odds['jra-odds.json'])
            with patch.object(c,'ROOT',work):
                c.publish('weights',before,weights,{'stage':'weights','date':c.u.DATE,'success':True})
                # The odds job deliberately uses the old checkout: publishing
                # must merge with the weight job's already committed result.
                c.publish('odds',before,odds,{'stage':'odds','date':c.u.DATE,'success':True})
                c.publish('histories',before,before,{'stage':'histories','date':c.u.DATE,'success':False,'error':'simulated profile source failure'})
            head=git('rev-parse','origin/main');result=json.loads(git('show',head+':dist/feed.json'))
            self.assertEqual(result['jra']['horses'][0]['weight'],'456')
            self.assertEqual(result['jra']['horses'][0]['winOdds'],2.5)
            self.assertEqual(result['jra']['horses'][0]['note'],'saved')
            self.assertEqual(result['jraOdds']['jra-r']['単勝']['1'],[2.5])
            metadata=json.loads(git('show',head+':dist/update-status/latest.json'))
            self.assertEqual(metadata['checkedAt'],result['checkedAt'])
            self.assertEqual(metadata['version'],1)
            self.assertLess(len(json.dumps(metadata)),300)
            self.assertTrue(result['results'][0]['partial']) # Other jobs haven't run.
            self.assertEqual(git('diff','--name-only'),'') # Never resets/stashes a working checkout.

if __name__=='__main__':unittest.main()

