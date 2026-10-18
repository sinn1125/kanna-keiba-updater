"""Update official JRA data with independent, validated retry transactions."""
import datetime
import json
from pathlib import Path
import subprocess
import sys
import importlib.util
import os
import re
import time

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
DATE = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()

def parse(path):
    text = path.read_text()
    return json.loads(text[text.index('{'):text.rindex('}') + 1])

DEADLINE = None
SNAPSHOTS = ('results-data.js', 'jra-odds.json', 'jra-forecasts.json')

def snapshot():
    return {n: (DIST/n).read_bytes() if (DIST/n).exists() else None for n in SNAPSHOTS}

def restore(saved):
    for name, content in saved.items():
        if content is None: (DIST/name).unlink(missing_ok=True)
        else: atomic_write(DIST/name, content)

def atomic_write(path, content):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(content if isinstance(content, bytes) else content.encode())
    temporary.replace(path)

def run(*args):
    remaining = DEADLINE - time.monotonic() if DEADLINE else 90
    if remaining < 5: raise TimeoutError('Update time budget exhausted; retry next scheduled run')
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True, timeout=min(max(1,min(360,int(os.environ.get("JRA_STAGE_TIMEOUT","90")))), remaining))

def stage(name, args, validate):
    """Retry only this transaction; never keep its failed/invalid file writes."""
    saved = snapshot()
    for attempt in range(1, 3):
        try:
            run(*args)
            validate()
            return {'stage': name, 'success': True, 'attempts': attempt}
        except Exception as error:
            restore(saved)
            print(f'STAGE {name} attempt {attempt}: {type(error).__name__}: {error}', flush=True)
            if attempt == 2 or (DEADLINE and DEADLINE-time.monotonic() < 10):
                return {'stage': name, 'success': False, 'attempts': attempt,
                        'error': f'{type(error).__name__}: {error}'}
            time.sleep(2)

def verify_data(previous):
    verify(DIST/'results-data.js')
    verify_preservation(previous, parse(DIST/'results-data.js'), DATE)

def verify_forecasts():
    fresh = parse(DIST/'results-data.js')
    forecasts = json.loads((DIST/'jra-forecasts.json').read_text())
    by_race = {r['id']:r for r in fresh['races'] if r['date']==DATE}
    assert set(by_race) <= set(forecasts), 'Missing AI forecast for a race'
    for rid, race in by_race.items():
        hs = [h for h in fresh['horses'] if h['raceId']==rid and h.get('entryState')!='not_listed']
        assert len(hs)==race['field'] and all(h.get('historyComplete') for h in hs), ('Incomplete profiles',rid)
        forecast = forecasts[rid]
        assert set(forecast['items'])=={h['id'] for h in hs}, ('Forecast runners',rid)
        assert forecast.get('forecastKind') in ('pre-race','retrospective'), ('Forecast provenance',rid)
        for item in forecast['items'].values():
            for prior in item['evidence']['recent']:
                assert prior['date']<DATE, ('Future run in AI',rid)
                assert all(x['next'] is None or x['next']['date']<DATE for x in prior['rivals']), ('Future rival result',rid)

def verify_odds():
    fresh = parse(DIST/'results-data.js')
    odds = json.loads((DIST/'jra-odds.json').read_text())
    assert set(odds['odds']).issubset({r['id'] for r in fresh['races']}), 'Unknown odds race'
    for markets in odds['odds'].values():
        for values in markets.values():
            assert all(isinstance(v,list) and 1<=len(v)<=2 and all(isinstance(n,(int,float)) and not isinstance(n,bool) and n>0 and n<float('inf') for n in v) for v in values.values()), 'Invalid odds'

def feed_forecasts(data, forecasts):
    """Do not publish a saved prediction against a changed/missing runner list."""
    races = {r['id'] for r in data['races']}
    return {rid: f for rid, f in forecasts.items() if rid in races and
            set(f.get('items',{})) == {h['id'] for h in data['horses']
                if h['raceId']==rid and h.get('entryState')!='not_listed'}}

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
    old = snapshot()
    previous = parse(DIST/'results-data.js')
    stages = []
    steps = [
        ('entries', ('scripts/import-jra-entries.py','--date',DATE), lambda: verify_data(previous)),
        ('histories', ('scripts/refresh-jra-entry-histories.py',DATE), lambda: verify_data(previous)),
        ('forecasts', ('scripts/forecast-current-jra.py',DATE), verify_forecasts),
        ('results', ('scripts/import-jra-results.py',DATE), lambda: verify_data(previous)),
        ('odds', ('scripts/import-jra-odds.py',DATE), verify_odds),
    ]
    for name, args, validator in steps:
        stages.append(stage(name, args, validator))
    try:
        verify_data(previous)
        fresh = parse(DIST/'results-data.js')
        odds = json.loads((DIST/'jra-odds.json').read_text()) if (DIST/'jra-odds.json').exists() else {'odds':{}}
        if (DIST/'jra-odds.json').exists(): verify_odds()
        forecasts = feed_forecasts(fresh, json.loads((DIST/'jra-forecasts.json').read_text()) if (DIST/'jra-forecasts.json').exists() else {})
        by_race = {r['id']:r for r in fresh['races'] if r['date']==DATE}
        errors = [x for x in stages if not x['success']]
        # 'success' means this snapshot passed preservation/structural checks.
        # Partial source failures remain explicit and make the Action fail AFTER
        # committing validated data, so successful independent updates survive.
        return {'organization':kind, 'success':any(x['success'] for x in stages),
                'partial':bool(errors), 'stages':stages,
                'changed':old != snapshot(),
                'races':len(fresh['races']), 'horses':len(fresh['horses']),
                'confirmedToday':sum(r['date']==DATE and r['status']=='results' for r in fresh['races']),
                'oddsValues':sum(len(v) for m in odds['odds'].values() for v in m.values()),
                'profilesComplete':sum(h.get('historyComplete') is True for h in fresh['horses'] if h['raceId'] in by_race),
                'forecasts':sum(rid in forecasts for rid in by_race),
                'forecastPredicted':sum(forecasts[rid].get('status')=='predicted' for rid in by_race if rid in forecasts)}
    except Exception as error:
        restore(old)
        return {'organization':kind, 'success':False, 'partial':True, 'stages':stages,
                'error':f'{type(error).__name__}: {error}'}

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
    if not links: raise ValueError('Official meeting index has no readable meeting links')
    return any(date.replace('-','') in cn for cn in links)

def main():
    global DEADLINE
    DEADLINE = time.monotonic() + 420
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
    try:
        meeting = has_meeting(DATE)
    except Exception as error:
        # Previously observed official entries are safe evidence of today's
        # meeting when the index request is temporarily unavailable.
        meeting = any(r['date']==DATE for r in parse(DIST/'results-data.js')['races'])
        if not meeting: raise
        print(f'MEETING INDEX unavailable; using saved official meeting: {error}', flush=True)
    if not meeting:
        report = {'date':DATE, 'status':'no-meeting', 'checkedAt':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(), 'results':[]}
        (ROOT/'update-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
        return
    results = [update('jra')]
    odds = json.loads((DIST/'jra-odds.json').read_text()) if (DIST/'jra-odds.json').exists() else {'odds':{},'meta':{}}
    feed = {'version': 1, 'checkedAt': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds'), 'date': DATE, 'results': results,
            'jra': parse(DIST / 'results-data.js'), 'jraOdds':odds['odds'], 'jraOddsMeta':odds['meta'],
            'jraForecasts':feed_forecasts(parse(DIST/'results-data.js'), json.loads((DIST/'jra-forecasts.json').read_text()) if (DIST/'jra-forecasts.json').exists() else {})}
    feed_path = DIST/'feed.json'
    before = json.loads(feed_path.read_text()) if feed_path.exists() else {}
    meaningful = lambda f:{k:f.get(k) for k in ('date','jra','jraOdds','jraOddsMeta','jraForecasts','results')}
    if any(r['success'] for r in results) and meaningful(before)!=meaningful(feed):atomic_write(feed_path,json.dumps(feed,ensure_ascii=False,separators=(',',':'))+'\n')
    # Only public JRA entry fields reach the separate read-only race-card Site.
    spec = importlib.util.spec_from_file_location('public_racecards', ROOT/'scripts/public-racecards.py')
    racecard_builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(racecard_builder)
    if feed_path.exists():
        racecard_builder.write_public_racecards(json.loads(feed_path.read_text()), DIST/'public-racecards.json')
    report = {'date':DATE,'checkedAt':feed['checkedAt'],'results':results,'event':os.environ.get('GITHUB_EVENT_NAME','local')}
    (ROOT/'update-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as output:output.write('### JRA update\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n')
    print(json.dumps({'date': DATE, 'results': results}, ensure_ascii=False), flush=True)
    if any(not r['success'] or r.get('partial') for r in results):
        sys.exit(1)

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Includes failures before the first importer (e.g. calendar/network).
        report = {'date':DATE, 'success':False, 'error':f'{type(error).__name__}: {error}'}
        atomic_write(ROOT/'update-report.json',json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        sys.exit(1)

