"""Update official public data; failed organization updates never replace snapshots."""
import concurrent.futures
import datetime
import json
from pathlib import Path
import subprocess
import sys
import importlib.util
import os
import re

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
DATE = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()

def parse(path):
    text = path.read_text()
    return json.loads(text[text.index('{'):text.rindex('}') + 1])

def run(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True, timeout=480)

def verify(path):
    data = parse(path)
    races, horses = data['races'], data['horses']
    ids = {r['id'] for r in races}
    assert len(ids) == len(races), 'Duplicate race ID'
    assert len({h['id'] for h in horses}) == len(horses), 'Duplicate horse ID'
    assert all(h['raceId'] in ids for h in horses), 'Unknown race'
    for r in races:
        datetime.date.fromisoformat(r['date'])
        assert r['status'] in ('entries', 'results')
        # Keep a withdrawn horse's stable ID and notes, but do not count it
        # among the currently listed runners.
        hs = [h for h in horses if h['raceId'] == r['id'] and h.get('entryState') != 'not_listed']
        assert len(hs) == int(r['field']), ('Incomplete field', r['id'])
        if r['status'] == 'entries':
            assert not r.get('payouts')
            assert not any(str(h.get('finish', '')).isdigit() for h in hs)

def update(kind):
    names = ['results-data.js', 'jra-odds.json']
    old = {name: (DIST / name).read_bytes() if (DIST / name).exists() else None for name in names}
    try:
        run('scripts/import-jra-entries.py', '--date', DATE)
        run('scripts/import-jra-results.py', DATE)
        run('scripts/import-jra-odds.py', DATE)
        verify(DIST / 'results-data.js')
        previous = json.loads(old['results-data.js'].decode().split('=',1)[1].rstrip(';\n'))
        fresh = parse(DIST/'results-data.js')
        verify_preservation(previous, fresh, DATE)
        odds = json.loads((DIST/'jra-odds.json').read_text())
        assert set(odds['odds']).issubset({r['id'] for r in fresh['races']}), 'Unknown odds race'
        for markets in odds['odds'].values():
            for values in markets.values():
                assert all(isinstance(v,list) and 1<=len(v)<=2 and all(isinstance(n,(int,float)) and n>0 for n in v) for v in values.values()), 'Invalid odds'
        return {'organization': kind, 'success': True, 'changed': any(old[n] != (DIST/n).read_bytes() for n in names),
                'races':len(fresh['races']), 'horses':len(fresh['horses']),
                'confirmedToday':sum(r['date']==DATE and r['status']=='results' for r in fresh['races']),
                'oddsValues':sum(len(v) for m in odds['odds'].values() for v in m.values())}
    except Exception as error:
        for name, content in old.items():
            if content is None:(DIST/name).unlink(missing_ok=True)
            else:(DIST / name).write_bytes(content)
        return {'organization': kind, 'success': False, 'error': str(error)}

def verify_preservation(old, new, date):
    old_races = {r['id']:r for r in old['races']}
    for kind in ('races','horses'):
        rows = {x['id']:x for x in new[kind]}
        for x in old[kind]:
            assert x['id'] in rows, ('Removed ID',x['id'])
            y = rows[x['id']]
            day = x.get('date') if kind=='races' else old_races[x['raceId']]['date']
            if day < date:assert x==y, ('Changed history',x['id'])
            if kind=='horses':
                for key in ('mark','note','history'):
                    if x.get(key):assert x[key]==y.get(key), ('Changed saved field',x['id'],key)

def has_meeting(date):
    spec = importlib.util.spec_from_file_location('profiles', ROOT/'scripts/import-profiles.py')
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    page = p.request_jra('D','pw01dli00/F3')
    links = re.findall(r"doAction\('/JRADB/accessD.html',\s*'(pw01drl[^']+)'",page)
    return any(date.replace('-','') in cn for cn in links)

def main():
    DIST.mkdir(exist_ok=True)
    # Bootstrap an independent official feed. Existing app/browser records stay
    # in the app; no authentication or Site scraping is required by the runner.
    seeds = {
        'results-data.js': 'const RESULT_DATA={"races":[],"horses":[]};\n',
    }
    for name, content in seeds.items():
        path = DIST / name
        if not path.exists():
            path.write_text(content)
    if not has_meeting(DATE):
        report = {'date':DATE, 'status':'no-meeting', 'checkedAt':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(), 'results':[]}
        (ROOT/'update-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
        return
    results = [update('jra')]
    odds = json.loads((DIST/'jra-odds.json').read_text()) if (DIST/'jra-odds.json').exists() else {'odds':{},'meta':{}}
    feed = {'version': 1, 'checkedAt': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds'), 'date': DATE, 'results': results,
            'jra': parse(DIST / 'results-data.js'), 'jraOdds':odds['odds'], 'jraOddsMeta':odds['meta']}
    feed_path = DIST/'feed.json'
    before = json.loads(feed_path.read_text()) if feed_path.exists() else {}
    meaningful = lambda f:{k:f.get(k) for k in ('date','jra','jraOdds','jraOddsMeta','results')}
    if meaningful(before)!=meaningful(feed):feed_path.write_text(json.dumps(feed,ensure_ascii=False,separators=(',',':'))+'\n')
    report = {'date':DATE,'checkedAt':feed['checkedAt'],'results':results,'event':os.environ.get('GITHUB_EVENT_NAME','local')}
    (ROOT/'update-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as output:output.write('### JRA update\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n')
    print(json.dumps({'date': DATE, 'results': results}, ensure_ascii=False), flush=True)
    if any(not r['success'] for r in results):
        sys.exit(1)

if __name__ == '__main__':
    main()

