"""Cancellation rows may have no published number; never fail the whole feed."""
import ast
import importlib.util
import math
import re
import unittest
from pathlib import Path

class CancelledForecast(unittest.TestCase):
    def test_blank_cancelled_number_is_unscored(self):
        source=Path(__file__).resolve().parents[1]/'scripts/forecast-current-jra.py'
        tree=ast.parse(source.read_text())
        predict=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='predict')
        model={'mean':[0],'scale':[1]}
        for kind in ('win','place','podium'):model[kind+'Intercept']=0;model[kind+'Coefficients']=[0]
        spec=importlib.util.spec_from_file_location('going',source.parent/'track-condition.py')
        going=importlib.util.module_from_spec(spec);spec.loader.exec_module(going)
        env={'track_condition':going,'re':re,'math':math,'norm':lambda s:s,'grade':lambda s:1,'RACES':{},'current':{'models':{'平地':model}},'g':{'style':lambda *args:None,'past':lambda *args:[],'analyse':lambda *args:([0],{'recent':[{'date':'2026-09-01'}]})}}
        exec(compile(ast.Module(body=[predict],type_ignores=[]),str(source),'exec'),env)
        horses=[{'id':str(n),'name':'馬'+str(n),'horseCname':f'pw01dud00202400000{n}/AA','history':[{'date':'2026-09-01','finish':'2'}],'number':str(n),'entryState':'listed'} for n in range(1,4)]
        horses[0].update(number='',entryState='cancelled',finish='取消')
        race={'id':'jra-test','date':'2026-10-17','course':'東京','number':5,'name':'試験','distance':1600,'surface':'芝'}
        result,missing=env['predict'](race,race,horses)
        self.assertEqual(result['status'],'predicted')
        self.assertIsNone(result['items']['1']['number'])
        self.assertIsNone(result['items']['1']['winScore'])
        self.assertEqual(result['items']['1']['mark'],'')
        self.assertEqual(len(result['items']),3)
        self.assertIn('馬1',missing)

if __name__=='__main__':unittest.main()

