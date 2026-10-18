import ast
import datetime
import unittest
from pathlib import Path


source=Path(__file__).resolve().parents[1]/'scripts/import-jra-odds.py'
tree=ast.parse(source.read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='remember_pre_start')
env={'datetime':datetime.datetime,'JST':datetime.timezone(datetime.timedelta(hours=9))}
exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),env)
remember=env['remember_pre_start']
URL='https://www.jra.go.jp/JRADB/accessO.html?CNAME=pw151ou-test'


class PreStartOddsTests(unittest.TestCase):
    def setUp(self):
        self.race={'id':'jra-2026-10-18-tokyo-1','date':'2026-10-18','startTime':'10:05'}
        self.old={'odds':{},'meta':{}}

    def meta(self,time):
        return {'fetchedAt':'2026-10-18T'+time+'+09:00','sources':{'単勝':URL,'馬連':URL},'coverage':{'単勝':'official-listed','馬連':'official-listed'}}

    def test_last_pre_start_per_market_and_no_post_start(self):
        rid=self.race['id']
        self.assertEqual(remember(self.old,self.race,rid,{'単勝':{'1':[2.0]}},self.meta('09:55:00')),1)
        self.assertEqual(remember(self.old,self.race,rid,{'単勝':{'1':[2.2]},'馬連':{'1-2':[7.4]}},self.meta('10:04:59')),2)
        self.assertEqual(remember(self.old,self.race,rid,{'単勝':{'1':[10.0]}},self.meta('10:05:00')),0)
        self.assertEqual(self.old['preRace'][rid]['単勝']['odds']['1'],[2.2])
        self.assertEqual(self.old['preRace'][rid]['馬連']['odds']['1-2'],[7.4])

    def test_missing_start_and_missing_market_are_withheld(self):
        rid=self.race['id']
        self.assertEqual(remember(self.old,{'date':self.race['date']},rid,{'単勝':{'1':[2]}},self.meta('09:40:00')),0)
        self.assertEqual(remember(self.old,self.race,rid,{'単勝':{}},self.meta('09:40:00')),0)
        self.assertEqual(self.old.get('preRace',{}).get(rid,{}),{})

    def test_older_observation_cannot_replace_newer(self):
        rid=self.race['id']
        remember(self.old,self.race,rid,{'単勝':{'1':[2.2]}},self.meta('10:04:59'))
        self.assertEqual(remember(self.old,self.race,rid,{'単勝':{'1':[2.0]}},self.meta('09:40:00')),0)
        self.assertEqual(self.old['preRace'][rid]['単勝']['odds']['1'],[2.2])


if __name__=='__main__':unittest.main()
