"""Apply the existing validated model to current JRA entries using official archive starts.

The model coefficients and feature equations are reused unchanged. Races whose
five most recent central starts cannot be reconciled with JRA horse profiles
are withheld, rather than assigned invented evidence.
"""
import ast
import bisect
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import gzip
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
import importlib.util
_spec = importlib.util.spec_from_file_location('track_condition', ROOT/'scripts/track-condition.py')
track_condition = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(track_condition)
_coverage_spec = importlib.util.spec_from_file_location('workout_coverage', ROOT/'scripts/workout-coverage.py')
workout_coverage = importlib.util.module_from_spec(_coverage_spec)
_coverage_spec.loader.exec_module(workout_coverage)
# Optional manually entered, provenance-bearing workout records; no source means unknown.
workout_path = ROOT/'dist/workout-records.json'
workout_records = json.loads(workout_path.read_text()) if workout_path.exists() else {}
source = json.loads((ROOT/'dist/results-data.js').read_text().split('=',1)[1].rstrip(';\n'))
current = json.loads((ROOT/'dist/ai-model-base.json').read_text())
forecast_path = ROOT/'dist/jra-forecasts.json'
current['races'] = json.loads(forecast_path.read_text()) if forecast_path.exists() else {}

# Reuse exactly the model's feature definitions; do not silently invent a
# second formula for currently published entries.
tree = ast.parse((ROOT/'scripts/build-ai-predictions.py').read_text())
names = {'norm','sec','grade','course_meta','similar','perf','weighted','style','rivals','analyse'}
selected = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
g = {'re':re,'math':math,'Counter':Counter,'unicodedata':unicodedata}
g['META'] = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='META' for t in n.targets))
exec(compile(ast.Module(body=selected,type_ignores=[]),str(ROOT/'scripts/build-ai-predictions.py'),'exec'),g)
norm,sec,grade=g['norm'],g['sec'],g['grade']

RACES={};HISTORY=defaultdict(list)
for file in sorted((ROOT/'dist/model-context').glob('20??.json.gz'))+[None]:
    data=json.load(gzip.open(file,'rt')) if file else source
    horses=defaultdict(list)
    for h in data['horses']:horses[h['raceId']].append(h)
    for item in data['races']:
        if item['status']!='results':continue
        key=f"{item['date']}|{item['course']}|{item['number']}"
        if key in RACES:
            if item.get('weather'):RACES[key]['weather']=item['weather']
            continue
        route=item.get('track','')
        surface='障害' if '障害' in item.get('surface','') or '障害' in route else 'ダート' if 'ダート' in item.get('surface','') or 'ダート' in route else '芝'
        r={'id':key,'date':item['date'],'course':item['course'],'number':item['number'],'name':item['name'],
           'weather':item.get('weather',''),'going':item.get('going'),'distance':int(item['distance']),'surface':surface,'route':route,'grade':grade(str(item.get('grade',''))+' '+item.get('className','')),'runners':[]}
        for h in horses[item['id']]:
            if h.get('entryState')=='not_listed':continue
            identity=h['id'].split('-horse-')[-1][-10:]
            if not identity.isdigit():continue
            finish=str(h.get('finish',''))
            passing=[int(x) for x in str(h.get('passing','')).split('-') if x.isdigit()]
            r['runners'].append({'horseId':identity+'|'+norm(h['name']),'name':norm(h['name']),
                                 'number':int(h['number']) if str(h.get('number','')).isdigit() else 0,
                                 'finish':int(finish) if finish.isdigit() else None,
                                 'time':sec(str(h.get('time',''))),'last3f':sec(str(h.get('last3f',''))),'passing':passing,
                                 'raceId':key,'date':item['date']})
        r['field']=len(r['runners'])
        if r['field']!=item['field']:raise ValueError(f'Archive field mismatch: {key}')
        winner=next((h['time'] for h in r['runners'] if h['finish']==1),None)
        fts=sorted(h['last3f'] for h in r['runners'] if h['last3f'] is not None)
        for h in r['runners']:
            h['margin']=max(0,h['time']-winner) if h['time'] is not None and winner is not None else None
            h['lateRank']=bisect.bisect_left(fts,h['last3f'])/(len(fts)-1) if h['last3f'] is not None and len(fts)>1 else None
            HISTORY[h['horseId']].append(h)
        RACES[key]=r

for starts in HISTORY.values():
    starts.sort(key=lambda h:(h['date'],h['raceId']))
    for i,h in enumerate(starts):h['next']=starts[i+1] if i+1<len(starts) else None
g['RACES']=RACES;g['HISTORY']=HISTORY
g['past']=lambda hid,date:[h for h in HISTORY.get(hid,()) if h['date']<date]

def predict(info, target, hs):
    rid=f"{info['date']}|{info['course']}|{info['number']}"
    surface='障害' if '障害' in info.get('surface','') else 'ダート' if 'ダート' in info.get('surface','') else '芝'
    r={'id':rid,'date':info['date'],'course':info['course'],'number':info['number'],'name':info['name'],
       'weather':info.get('weather',''),'going':info.get('going'),'distance':int(info['distance']),'surface':surface,'route':info.get('track',''),
       'grade':grade(str(info.get('grade',''))+' '+info.get('className','')),'field':len(hs),'runners':[]}
    for h in hs:
        m=re.search(r'pw01dud(\d{11,13})/',h.get('horseCname',''))
        if not m:raise ValueError(f"Missing horse identity {h['id']}")
        r['runners'].append({'horseId':m[1][-10:]+'|'+norm(h['name']),'name':norm(h['name'])})
    # Preserve archived results used as history for later forecast targets.
    if rid not in RACES:RACES[rid]=r
    styles={h['horseId']:g['style'](list(reversed(g['past'](h['horseId'],r['date']))),surface) for h in r['runners']}
    group='障害' if surface=='障害' else '平地';model=current['models'][group]
    items={};available=[];withheld=[]
    def probability(x,name):
        z=model[name+'Intercept']+sum(((v-m)/s if s else 0)*c for v,m,s,c in zip(x,model['mean'],model['scale'],model[name+'Coefficients']))
        return 1/(1+math.exp(-max(-100,min(100,z))))
    for h in hs:
        hid=re.search(r'pw01dud(\d{11,13})/',h['horseCname'])[1][-10:]+'|'+norm(h['name'])
        x,ev=g['analyse'](hid,r,styles,True)
        latest=sorted((z['date'] for z in h['history'] if z['date']<r['date'] and not any(w in str(z.get('finish','')) for w in ('取消','除外'))),reverse=True)[:5]
        observed=[z['date'] for z in ev['recent']]
        eligible=bool(observed) and observed==latest and h.get('finish') not in ('取消','除外') and h.get('entryState') not in ('cancelled','not_listed')
        item={'mark':'','winScore':probability(x,'win') if eligible else None,
              'placeScore':probability(x,'place') if eligible else None,
              'podiumScore':probability(x,'podium') if eligible else None,
              'evidence':ev,'features':x,'number':int(h['number']) if str(h.get('number','')).isdigit() else None}
        condition = track_condition.adjustment(g, hid, r)
        item['evidence']['trackCondition'] = condition
        for score in ('winScore','placeScore','podiumScore'):
            item[score] = track_condition.apply_probability(item[score], condition)
        items[h['id']]=item
        if eligible:available.append((h['id'],item))
        else:withheld.append(h['name'])
        if any(z['date']>=r['date'] for z in ev['recent']):raise ValueError('Post-race data in forecast')
    ranked=[]
    if len(available)>=max(2,math.ceil(len(hs)/2)):
        first=max(available,key=lambda z:(z[1]['winScore'],-(z[1]['number'] or 999)));first[1]['mark']='◎';ranked=[first[0]]
        rest=sorted((z for z in available if z[0]!=first[0]),key=lambda z:(-z[1]['placeScore'],z[1]['number'] or 999))
        for (hid,x),mark in zip(rest[:4],('○','▲','△','△')):x['mark']=mark;ranked.append(hid)
    # Missing historical evidence does not imply a bad horse or a bad workout.
    # Untrained priors remain visible as provisional context, never as a validated ◎.
    active_ids = [h['id'] for h in hs if h.get('entryState') not in ('cancelled', 'not_listed')]
    items, fallback = workout_coverage.fallback_scores(items, active_ids)
    race_workouts = workout_records.get(info['id'], {}) if isinstance(workout_records, dict) else {}
    if not isinstance(race_workouts, dict):
        race_workouts = {}
    coverage = workout_coverage.workout_status(active_ids, race_workouts)
    return {'asOf':info['date'],'group':group,'status':'predicted' if ranked else 'insufficient',
            'unknownHorses':len(withheld),'items':items,'ranked':ranked,
            'dataQuality':{'historyFallback':fallback,'workout':coverage,
                           'fallbackIsValidatedModel':False,
                           'betRecommendationEligible':fallback['mode']=='original' and coverage['coverage']!='unknown'}},withheld

# Load the exact extra equations used by an adopted challenger.
if current['models']['平地'].get('featureVersion')=='enhanced-v1':
    import importlib.util
    spec=importlib.util.spec_from_file_location('enhancements',ROOT/'scripts/ml-enhancements.py')
    extra=importlib.util.module_from_spec(spec);spec.loader.exec_module(extra)
    extra.install(g)

dates=set(sys.argv[1:] or ['2026-10-10','2026-10-11'])
now = datetime.now(timezone(timedelta(hours=9)))
targets=[r for r in source['races'] if r['date'] in dates and
         (r['id'] not in current['races'] or track_condition.before_start(r, now))]
if not targets:
    print('FORECAST 0 missing races')
    sys.exit(0)
for r in targets:
    hs=[h for h in source['horses'] if h['raceId']==r['id'] and h.get('entryState')!='not_listed']
    if len(hs)!=r['field'] or any(not h.get('historyComplete') for h in hs):raise ValueError('Entry or profile mismatch')
    forecast,missing=predict(r,r,hs)
    start_time=r.get('startTime','')
    post_time=(not re.fullmatch(r'\d{2}:\d{2}',start_time) or datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M')>=r['date']+' '+start_time)
    if r['status']=='results' or post_time:
        # A reconstruction from strictly earlier race dates is useful for
        # validation, but it was not a saved pre-post-time forecast.
        forecast['forecastKind']='retrospective'
    else:forecast['forecastKind']='pre-race'
    forecast['forecastGeneratedAt']=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')
    current['races'][r['id']]=forecast
    if missing:print('WITHHELD',r['id'],len(missing),flush=True)
current['fetchedAt']=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')
forecast_path.write_text(json.dumps(current['races'],ensure_ascii=False,separators=(',',':'))+'\n')
print('FORECAST',len(targets),'races',sum(x['status']=='predicted' for x in (current['races'][r['id']] for r in targets)),'predicted',len(RACES),'official result races')

