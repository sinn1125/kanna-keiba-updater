import copy
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
update=module('update','update.py')
odds=module('odds','import-jra-odds.py')
public=module('public_racecards','public-racecards.py')

class Protections(unittest.TestCase):
    def test_preserves_ids_notes_and_history(self):
        before={'races':[{'id':'jra-r','date':'2026-10-11'}],'horses':[{'id':'jra-h','raceId':'jra-r','mark':'◎','note':'記録','history':[{'date':'2026-09-01'}]}]}
        update.verify_preservation(before,copy.deepcopy(before),'2026-10-12')
        broken=copy.deepcopy(before);broken['horses'][0]['note']=''
        with self.assertRaises(AssertionError):update.verify_preservation(before,broken,'2026-10-12')
        broken=copy.deepcopy(before);broken['horses']=[]
        with self.assertRaises(AssertionError):update.verify_preservation(before,broken,'2026-10-12')
    def test_official_range_odds(self):
        self.assertEqual(odds.numbers('21.1-22.5'),[21.1,22.5])
        self.assertIsNone(odds.numbers('取消'))
        self.assertIsNone(odds.numbers('未発表'))
    def test_pair_and_triple_tables(self):
        frame='<table class="waku"><caption><img alt="枠1白"></caption><tbody><tr><th>2</th><td>41.1</td></tr></tbody></table>'
        self.assertEqual(odds.parse_market(frame,'waku'),{'1-2':[41.1]})
        html='<table class="wide"><caption>1</caption><tbody><tr><th>2</th><td>21.1-22.5</td></tr></tbody></table>'
        self.assertEqual(odds.parse_market(html,'wide'),{'1-2':[21.1,22.5]})
        html='<table class="tan3 pop"><tbody><tr><td class="num">9-1-18</td><td class="odds">112.3</td></tr></tbody></table>'
        self.assertEqual(odds.parse_market(html,'tan3'),{'9-1-18':[112.3]})
    def test_49_scheduled_slots_on_any_official_meeting_day(self):
        text=(ROOT/'.github/workflows/update.yml').read_text()
        self.assertIn("'*/10 0-7 * * *'",text)
        self.assertIn("'0 8 * * *'",text)
        slots=[(h+9,m) for h in range(8) for m in range(0,60,10)]+[(17,0)]
        self.assertEqual(len(slots),49)
        self.assertEqual(slots[0],(9,0));self.assertEqual(slots[-1],(17,0))
    def test_public_racecards_exclude_private_and_prediction_fields(self):
        feed={'version':1,'checkedAt':'2026-10-13T12:00:00+09:00','jra':{
            'races':[{'id':'jra-test','date':'2026-10-13','course':'東京','number':1,'name':'競走','field':1,'status':'entries','payouts':[999], 'secret':'hidden'}],
            'horses':[{'id':'jra-h','raceId':'jra-test','name':'試験馬','number':'1','frame':1,'mark':'◎','note':'個人メモ','history':[{'date':'2026-01-01'}]}]},
            'jraForecasts':{'jra-test':{'private':'prediction'}},'jraOdds':{'jra-test':{'単勝':{}}}}
        result=public.racecards(feed)
        self.assertEqual(result['jra']['races'][0]['name'],'競走')
        self.assertEqual(result['jra']['horses'][0]['name'],'試験馬')
        for word in ('個人メモ','prediction','payouts','history','secret','jraOdds','"mark"'):
            self.assertNotIn(word,str(result))

if __name__=='__main__':unittest.main()
