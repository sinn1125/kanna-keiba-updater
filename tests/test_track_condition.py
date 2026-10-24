import importlib.util
from pathlib import Path
import unittest
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('condition',ROOT/'scripts/track-condition.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class GoingTests(unittest.TestCase):
    def test_surfaces_and_unknown(self):
        self.assertEqual(m.normalize_going('天候 晴 芝：稍重 ダート：不良','芝'),'稍重')
        self.assertEqual(m.normalize_going('天候 晴 芝：稍重 ダート：不良','ダート'),'不良')
        self.assertIsNone(m.normalize_going('晴','芝'))
        self.assertIsNone(m.normalize_going('芝：良','ダート'))
        self.assertEqual(m.normalize_going('重','ダート'),'重')

    def environment(self):
        races={};rows=[]
        for i in range(8):
            rows.append({'date':'2026-09-01','raceId':str(i),'finish':1 if i<4 else 8,'value':.9 if i<4 else .1})
            races[str(i)]={'surface':'芝','weather':'芝：重' if i<4 else '芝：良'}
        return {'past':lambda *args:rows,'RACES':races,'weighted':lambda hs:sum(h['value'] for h in hs)/len(hs)}

    def test_direction_shrink_and_future_surface_exclusion(self):
        g=self.environment();race={'date':'2026-10-24','surface':'芝','weather':'芝：重'}
        ev=m.adjustment(g,'horse',race)
        self.assertGreater(ev['logOddsAdjustment'],0)
        self.assertLessEqual(ev['logOddsAdjustment'],.25)
        self.assertGreater(m.apply_probability(.3,ev),.3)
        race['weather']='芝：良'
        self.assertLess(m.adjustment(g,'horse',race)['logOddsAdjustment'],0)
        g['RACES']['0']['surface']='障害'
        rows=g['past']();rows[1]['date']='2026-10-24';rows[2]['date']='2026-10-25'
        self.assertEqual(m.adjustment(g,'horse',race)['logOddsAdjustment'],0)

    def test_neutral_and_no_post_time_refresh(self):
        g=self.environment()
        self.assertEqual(m.adjustment(g,'horse',{'date':'2026-10-24','surface':'芝'})['logOddsAdjustment'],0)
        r={'date':'2026-10-24','status':'entries','startTime':'12:00'}
        self.assertTrue(m.before_start(r,datetime(2026,10,24,11,59)))
        self.assertFalse(m.before_start(r,datetime(2026,10,24,12)))
        self.assertFalse(m.before_start({**r,'status':'results'},datetime(2026,10,24,11)))

if __name__=='__main__':unittest.main()
