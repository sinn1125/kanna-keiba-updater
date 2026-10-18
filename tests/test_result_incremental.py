"""A published first race must not wait for the rest of the meeting."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('incremental_results', ROOT/'scripts/import-jra-results.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
DATE = '2026-10-18'
ONE = 'pw01sde0105202604070120261018/AA'
TWO = 'pw01sde0105202604070220261018/BB'
DAY = 'pw01srl10052026040720261018/CC'


def unit(number, name, refund=True):
    return (f'<div class="race_result_unit"><div class="race_header"><img alt="{number}レース" />'
            '<div class="baba">晴 ダート良</div></div><table class="basic"><tbody><tr>'
            f'<td class="place">1</td><td class="num">{number}</td><td class="horse">{name}</td>'
            '<td class="time">1:24.0</td><td class="h_weight">460(+2)</td>'
            '<td class="margin"></td><td class="corner">1 1</td><td class="f_time">36.0</td>'
            '<td class="pop">2</td><td class="jockey">騎手</td><td class="trainer">厩舎</td>'
            '</tr></tbody></table><div class="refund_unit">'
            + ('<li>単勝 1 200円 2番人気</li>' if refund else '') + '</div>'
            '<div class="result_time_data"><tr><th>ハロンタイム</th><td>12.0 11.0</td></tr></div>'
            '<div class="result_corner_place"><tr><th>4コーナー</th><td>1-2</td></tr></div></div>')


class IncrementalResultTests(unittest.TestCase):
    def test_actual_links_and_each_race_checked_independently(self):
        self.assertEqual(mod.observed_cnames(f'xx {ONE} xx {ONE} yy {TWO}', 'pw01sde', DATE), [ONE,TWO])
        self.assertEqual(mod.observed_cnames(ONE, 'pw01sde', '2026-10-19'), [])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'dist').mkdir()
            races=[{'id':f'jra-{DATE}-tokyo-{n}','date':DATE,'field':1,'status':'entries'} for n in (1,2)]
            horses=[{'id':f'h{n}','raceId':r['id'],'name':f'馬{n}','number':str(n),
                     'mark':'◎' if n==1 else '', 'note':'残す', 'history':[{'date':'2026-10-17'}]}
                    for n,r in enumerate(races,1)]
            file=root/'dist/results-data.js'
            file.write_text('const RESULT_DATA='+json.dumps({'races':races,'horses':horses},ensure_ascii=False)+';\n')
            response={
                'pw01skl00999999/B3':'var yearMonth = "202610";',
                'pw01sli00/AF':f'<a href="/JRADB/accessS.html?CNAME={ONE}">result</a>',
                ONE:f'<a href="/JRADB/accessS.html?CNAME={DAY}">day</a>',
                DAY:(f'<h1>レース結果 レース選択 東京</h1><a href="/JRADB/accessS.html?CNAME={ONE}">1R</a>'
                     f'<a href="/JRADB/accessS.html?CNAME={TWO}">2R</a>'),
                TWO:unit(2,'馬2',refund=False),
            }
            # 1R is final, 2R is visible but still lacks official payout.
            response[ONE] += unit(1,'馬1')
            with patch.object(mod,'ROOT',root), patch.object(mod.p,'request_jra',side_effect=lambda k,c:response[c]), \
                 patch.object(sys,'argv',['import-jra-results.py',DATE]):
                mod.main()
            result=json.loads(file.read_text().split('=',1)[1].strip().rstrip(';'))
            self.assertEqual([r['status'] for r in result['races']],['results','entries'])
            self.assertEqual(result['horses'][0]['finish'],'1')
            self.assertEqual(result['horses'][0]['mark'],'◎')
            self.assertEqual(result['horses'][0]['note'],'残す')
            self.assertNotIn('finish',result['horses'][1])
            details=json.loads((root/'update-detail-results.json').read_text())
            self.assertEqual(details['newlyConfirmedRaces'],[races[0]['id']])
            self.assertEqual(details['status'],'partial')
            self.assertEqual(details['failedRaces'],[])
            self.assertEqual(details['pendingRaces'],[races[1]['id']])
            self.assertEqual(len(details['officialUrls']),2)


if __name__ == '__main__':unittest.main()
