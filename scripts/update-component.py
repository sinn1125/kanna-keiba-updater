"""Independent official JRA updater and conflict-safe snapshot publisher."""
import argparse
import copy
import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('update',ROOT/'scripts/update.py')
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
COMPONENTS=('entries','weights','histories','forecasts','results','odds')
FILES=('results-data.js','jra-odds.json','jra-forecasts.json')
MISSING=object()
PUBLICATION_DEADLINE=None


def decode(name, raw):
    if raw is None:
        return {'races':[],'horses':[]} if name=='results-data.js' else {'odds':{},'meta':{}} if name=='jra-odds.json' else {}
    text=raw.decode();return json.loads(text[text.index('{'):text.rindex('}')+1])


def encode(name, data):
    text=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    return (('const RESULT_DATA='+text+';\n') if name=='results-data.js' else text+'\n').encode()


def merge(before, after, latest, path=''):
    """Three-way field merge: changed concurrent values are never overwritten."""
    if before == after:return copy.deepcopy(latest) if latest is not MISSING else MISSING
    if latest == after:return copy.deepcopy(latest)
    if latest == before:return copy.deepcopy(after) if after is not MISSING else MISSING
    if path.endswith('.fetchedAt') and all(isinstance(x,str) for x in (after,latest)):
        return max((after,latest),key=lambda x:datetime.datetime.fromisoformat(x).timestamp())
    if isinstance(after,dict) and isinstance(latest,dict) and (before is MISSING or isinstance(before,dict)):
        before={} if before is MISSING else before;result=copy.deepcopy(latest)
        for key in set(before)|set(after):
            value=merge(before.get(key,MISSING),after.get(key,MISSING),latest.get(key,MISSING),path+'.'+key)
            if value is MISSING:result.pop(key,None)
            else:result[key]=value
        return result
    if path in ('results-data.js.races','results-data.js.horses') and all(isinstance(x,list) for x in (before,after,latest)):
        b={x['id']:x for x in before};a={x['id']:x for x in after};r={x['id']:copy.deepcopy(x) for x in latest}
        if not set(b)<=set(a):raise ValueError('Removed stable IDs: '+path)
        for key in a:
            value=merge(b.get(key,MISSING),a[key],r.get(key,MISSING),path+'.'+key)
            r[key]=value
        return list(r.values())
    raise ValueError('Concurrent update conflict; keep latest and retry next run: '+path)


def restrict(component, before, after):
    """Weights and entry fields have separate owners despite sharing a source."""
    result=copy.deepcopy(after)
    if component in ('entries','weights'):
        old={h['id']:h for h in before['horses']};fresh={h['id']:h for h in after['horses']}
        if component=='weights':
            result=copy.deepcopy(before)
            for horse in result['horses']:
                incoming=fresh.get(horse['id'],{})
                for key in ('weight','weightChange'):
                    if key in incoming:horse[key]=incoming[key]
        else:
            for horse in result['horses']:
                for key in ('weight','weightChange','winOdds','oddsState'):
                    if key in old.get(horse['id'],{}):horse[key]=old[horse['id']][key]
                    else:horse.pop(key,None)
    return result


def sync_single_odds(data, odds):
    current={r['id'] for r in data['races'] if r['date']==u.DATE and r['status']=='entries'}
    for horse in data['horses']:
        if horse['raceId'] not in current or horse.get('entryState') in ('cancelled','not_listed'):continue
        values=odds.get('odds',{}).get(horse['raceId'],{}).get('単勝',{}).get(str(horse.get('number','')))
        if values:
            horse['winOdds']=values[0];horse['oddsState']='current'


def validate(files, previous):
    with tempfile.TemporaryDirectory() as directory:
        saved=u.DIST;u.DIST=Path(directory)
        try:
            for name in FILES:(u.DIST/name).write_bytes(encode(name,files[name]))
            u.verify_data(previous)
            u.verify_odds()
        finally:u.DIST=saved


def git(*args, data=None, env=None, check=True):
    timeout=min(20,max(1,PUBLICATION_DEADLINE-time.monotonic())) if PUBLICATION_DEADLINE else 20
    return subprocess.run(['git',*args],cwd=ROOT,input=data,capture_output=True,check=check,env=env,timeout=timeout)


def repository_file(head,path):
    p=git('show',head+':'+path,check=False)
    return p.stdout if p.returncode==0 else None


def compose(files, statuses):
    data=files['results-data.js'];odds=files['jra-odds.json'];forecasts=u.feed_forecasts(data,files['jra-forecasts.json'])
    stages=[statuses.get(c,{'stage':c,'success':False,'status':'not-yet-run'}) for c in COMPONENTS]
    today=[r for r in data['races'] if r['date']==u.DATE];ids={r['id'] for r in today}
    result={'organization':'jra','success':True,'partial':any(not s.get('success') or s.get('date')!=u.DATE for s in stages),
            'stages':stages,'races':len(data['races']),'horses':len(data['horses']),
            'confirmedToday':sum(r['status']=='results' for r in today),'forecasts':sum(r in forecasts for r in ids),
            'forecastPredicted':sum(forecasts[r].get('status')=='predicted' for r in ids if r in forecasts),
            'profilesComplete':sum(h.get('historyComplete') is True for h in data['horses'] if h['raceId'] in ids)}
    return {'version':1,'checkedAt':now(),'date':u.DATE,'results':[result],'jra':data,
            'jraOdds':odds['odds'],'jraOddsMeta':odds['meta'],'jraForecasts':forecasts}


def now():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds')


def publish(component, before, after, report):
    """Build a commit from current remote state without resetting the checkout."""
    global PUBLICATION_DEADLINE
    PUBLICATION_DEADLINE=time.monotonic()+75
    for attempt in range(5):
        if time.monotonic()>=PUBLICATION_DEADLINE:break
        git('fetch','--quiet','origin','main')
        head=git('rev-parse','origin/main').stdout.decode().strip()
        latest={n:decode(n,repository_file(head,'dist/'+n)) for n in FILES}
        files={n:merge(before[n],after[n],latest[n],n) for n in FILES}
        validate(files,latest['results-data.js'])
        statuses={}
        for c in COMPONENTS:
            raw=repository_file(head,'dist/update-status/'+c+'.json')
            if raw:statuses[c]=json.loads(raw)
        statuses[component]=report
        changes={'dist/'+n:encode(n,files[n]) for n in FILES if files[n]!=latest[n]}
        changes['dist/update-status/'+component+'.json']=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
        if report.get('status')!='no-meeting':
            feed=compose(files,statuses)
            changes['dist/feed.json']=(json.dumps(feed,ensure_ascii=False,separators=(',',':'))+'\n').encode()
            spec=importlib.util.spec_from_file_location('public',ROOT/'scripts/public-racecards.py')
            public=importlib.util.module_from_spec(spec);spec.loader.exec_module(public)
            changes['dist/public-racecards.json']=(json.dumps(public.racecards(feed),ensure_ascii=False,separators=(',',':'))+'\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            env={**os.environ,'GIT_INDEX_FILE':str(Path(directory)/'index')}
            git('read-tree',head,env=env)
            for path,raw in changes.items():
                sha=git('hash-object','-w','--stdin',data=raw).stdout.decode().strip()
                git('update-index','--add','--cacheinfo','100644,'+sha+','+path,env=env)
            tree=git('write-tree',env=env).stdout.decode().strip()
            commit=git('-c','user.name=kanna-keiba-updater[bot]','-c','user.email=kanna-keiba-updater@users.noreply.github.com',
                       'commit-tree',tree,'-p',head,data=('Update verified JRA '+component+' snapshot\n').encode(),env=env).stdout.decode().strip()
            pushed=git('push','origin',commit+':refs/heads/main',check=False)
            if pushed.returncode==0:
                print(json.dumps({'component':component,'saved':True,'commit':commit,'attempt':attempt+1}),flush=True)
                return
            # Re-read the new remote state and merge the exact field delta again.
            print('Publication contention/transport error; retrying safe merge',flush=True)
            time.sleep(2)
    raise RuntimeError('Could not publish safely; next scheduled run will retry')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('component',choices=COMPONENTS)
    parser.add_argument('--publish',action='store_true');args=parser.parse_args();component=args.component
    before={n:decode(n,(u.DIST/n).read_bytes() if (u.DIST/n).exists() else None) for n in FILES}
    u.DEADLINE=time.monotonic()+420
    os.environ.setdefault('JRA_STAGE_TIMEOUT','360')
    report={'stage':component,'date':u.DATE,'checkedAt':now(),'success':False}
    after=copy.deepcopy(before)
    try:
        try:meeting=u.has_meeting(u.DATE)
        except Exception:
            if not any(r['date']==u.DATE for r in before['results-data.js']['races']):raise
            meeting=True
        if not meeting:report.update(success=True,status='no-meeting')
        else:
            names={'entries':'import-jra-entries.py','weights':'import-jra-entries.py','histories':'refresh-jra-entry-histories.py',
                   'forecasts':'forecast-current-jra.py','results':'import-jra-results.py','odds':'import-jra-odds.py'}
            command=('scripts/'+names[component],*(['--date',u.DATE] if component in ('entries','weights') else [u.DATE]))
            validator=u.verify_forecasts if component=='forecasts' else u.verify_odds if component=='odds' else lambda:u.verify_data(before['results-data.js'])
            if component in ('forecasts','histories') and not any(r['date']==u.DATE for r in before['results-data.js']['races']):
                raise ValueError('No official entries yet; this component will retry independently next run')
            status=u.stage(component,command,validator);report.update(status)
            after={n:decode(n,(u.DIST/n).read_bytes() if (u.DIST/n).exists() else None) for n in FILES}
            after['results-data.js']=restrict(component,before['results-data.js'],after['results-data.js'])
            if component=='odds':sync_single_odds(after['results-data.js'],after['jra-odds.json'])
            validate(after,before['results-data.js'])
    except Exception as error:
        after=copy.deepcopy(before);report.update(success=False,error=f'{type(error).__name__}: {error}')
    report['completedAt']=now()
    path=ROOT/('update-report-'+component+'.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if args.publish:
        try:
            publish(component,before,after,report)
            report['publicationSuccess']=True
        except Exception as error:
            report.update(success=False,publicationSuccess=False,error=f'{type(error).__name__}: {error}')
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('### '+component+'\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['success'] else 1

if __name__=='__main__':raise SystemExit(main())
