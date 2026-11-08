"""Read observed JRA odds links only. Missing markets keep their prior data."""
import concurrent.futures
import os
import importlib.util
import json
import math
import hashlib
import tempfile
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('profiles', ROOT/'scripts/import-profiles.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
JST = timezone(timedelta(hours=9))
VENUES = dict(zip(p.VENUES, ['sapporo','hakodate','fukushima','niigata','tokyo','nakayama','chukyo','kyoto','hanshin','kokura']))
TYPES = {'3':('枠連','waku'),'4':('馬連','umaren'),'5':('ワイド','wide'),'6':('馬単','umatan'),'7':('3連複','fuku3'),'8':('3連単','tan3')}

def links(html, prefix=''):
    values = re.findall(r"doAction\('/JRADB/accessO.html',\s*'([^']+)'", html)
    values += re.findall(r'href="/JRADB/accessO.html\?CNAME=([^"<>]+)"', html)
    return list(dict.fromkeys(cn for cn in values if cn.startswith(prefix)))

def numbers(text):
    text = text.replace(',', '').strip()
    if not re.fullmatch(r'\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?', text):
        return None
    values = [float(v) for v in text.split('-')]
    return values if all(v > 0 for v in values) else None

def parse_market(html, cls):
    root, result = p.tree(html), {}
    for table in root.all('table', cls):
        if 'pop' in table.a.get('class','').split():
            for row in table.first('tbody').all('tr'):
                key = row.first('td','num').text()
                value = numbers(row.first('td','odds').text())
                if re.fullmatch(r'\d+-\d+-\d+',key) and value:
                    result[key] = value
        else:
            caption = table.first('caption')
            key = caption.text()
            if cls=='waku' and not key:
                frame = re.search(r'枠(\d+)',caption.first('img').a.get('alt',''))
                key = frame[1] if frame else ''
            if not re.fullmatch(r'\d+(?:-\d+)?',key):
                continue
            for row in table.first('tbody').all('tr'):
                tail = row.first('th').text()
                value = numbers(row.first('td').text())
                if tail.isdigit() and value:
                    result[key+'-'+tail] = value
    return result

def identity(root, expected_date):
    header = root.first('div','race_header')
    date = re.search(r'(\d{4})年(\d+)月(\d+)日.*?(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)', header.text())
    number = re.search(r'(\d+)レース', header.first('img').a.get('alt',''))
    if not date or not number:
        raise ValueError('Official odds date/race header missing')
    observed = f'{date[1]}-{int(date[2]):02}-{int(date[3]):02}'
    if observed != expected_date:
        raise ValueError('Official odds target date mismatch')
    return f'jra-{observed}-{VENUES[date[4]]}-{number[1]}'

def read_race(cname, date):
    html = p.request_jra('O',cname)
    root = p.tree(html)
    rid = identity(root,date)
    odds, sources, coverage, markets = {}, {}, {}, {}
    checked=lambda:datetime.now(JST).isoformat(timespec='seconds')
    win, place = {}, {}
    for row in root.first('table','tanpuku').first('tbody').all('tr'):
        num = row.first('td','num').text()
        if not num.isdigit():
            continue
        for target,cls in ((win,'odds_tan'),(place,'odds_fuku')):
            value = numbers(row.first('td',cls).text())
            if value:
                target[num] = value
    if win: odds['単勝'] = win
    if place: odds['複勝'] = place
    for typ in ('単勝','複勝'):
        markets[typ]={'status':'success' if typ in odds else 'unpublished','checkedAt':checked()}
        if typ not in odds: continue
        markets[typ]['observedAt']=markets[typ]['checkedAt']
        sources[typ] = 'https://www.jra.go.jp/JRADB/accessO.html?CNAME='+cname
        coverage[typ] = 'official-listed'
    errors = []
    for key,(typ,cls) in TYPES.items():
        cn = next((x for x in links(html,'pw15'+key+'ou') if date.replace('-','') in x),None)
        if not cn:
            markets[typ]={'status':'unpublished','checkedAt':checked(),'reason':'公式券種リンク未掲載'}
            continue
        try:
            page = p.request_jra('O',cn)
            if key == '8':
                # Follow the observed popularity page, then measure coverage
                # against the actual listed runners instead of assuming it.
                pop = next(iter(links(page,'pw158op')),None)
                if not pop:
                    raise ValueError('Official trifecta popularity link missing')
                cn, page = pop, p.request_jra('O',pop)
            if identity(p.tree(page),date) != rid:
                raise ValueError('Official market race mismatch')
            values = parse_market(page,cls)
            markets[typ]={'status':'success' if values else 'unpublished','checkedAt':checked()}
            if values: markets[typ]['observedAt']=markets[typ]['checkedAt']
            if values:
                odds[typ] = values
                sources[typ] = 'https://www.jra.go.jp/JRADB/accessO.html?CNAME='+cn
                coverage[typ] = ('complete' if len(values)==math.perm(len(win),3) else 'partial-listed') if key=='8' else 'official-listed'
        except Exception as error:
            markets[typ]={'status':'error','checkedAt':checked(),'reason':str(error)}
            errors.append({'raceId':rid,'market':typ,'error':str(error)})
    return rid,odds,{'sources':sources,'coverage':coverage,'markets':markets,'fetchedAt':checked()},errors

def remember_pre_start(old, race, rid, values, meta):
    """Keep each market's last observed pre-start odds for later causal tests.

    A missing market never borrows a post-start price. This is a single compact
    snapshot per race and market, replaced only by a newer official observation.
    """
    if not race or not values or not race.get('startTime'):
        return 0
    try:
        start=datetime.fromisoformat(race['date']+'T'+race['startTime']).replace(tzinfo=JST)
        observed=datetime.fromisoformat(meta['fetchedAt'])
    except (TypeError,ValueError):
        return 0
    if observed.tzinfo is None or observed.date()!=start.date():
        return 0
    markets=old.setdefault('preRace',{}).setdefault(rid,{})
    saved=0
    for typ,priced in values.items():
        source=meta.get('sources',{}).get(typ)
        if not source or not priced:
            continue
        observed_at=meta.get('markets',{}).get(typ,{}).get('observedAt',meta['fetchedAt'])
        if datetime.fromisoformat(observed_at)>=start: continue
        previous=markets.get(typ,{})
        if previous.get('observedAt','')>=meta['fetchedAt']:
            continue
        markets[typ]={'observedAt':observed_at,'odds':priced,
                      'source':source,'coverage':meta.get('coverage',{}).get(typ,'official-listed')}
        snapshot=markets[typ]
        digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        old.setdefault('preRaceHistory',{}).setdefault(rid,{}).setdefault(typ,{})[digest]=dict(snapshot)
        saved+=1
    return saved

def run_main():
    date = sys.argv[1] if len(sys.argv)>1 else datetime.now(JST).date().isoformat()
    index = p.request_jra('O','pw15oli00/6D')
    meetings = [cn for cn in links(index,'pw15orl') if date.replace('-','') in cn]
    jobs = []
    for cn in meetings:
        page = p.request_jra('O',cn)
        jobs += [x for x in links(page,'pw151ou') if date.replace('-','') in x]
    jobs = list(dict.fromkeys(jobs))
    target = ROOT/'dist/jra-odds.json'
    old = json.loads(target.read_text()) if target.exists() else {'odds':{},'meta':{}}
    results=ROOT/'dist/results-data.js'
    published=json.loads(results.read_text().split('=',1)[1].rstrip(';\n')) if results.exists() else {'races':[]}
    race_index={r['id']:r for r in published['races'] if r['date']==date}
    count, errors, seen = 0, [], set()
    def safe_read(cn):
        try:return read_race(cn,date)
        except Exception as error:return None,{}, {},[{'cname':cn,'error':str(error)}]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(4,int(os.environ.get("JRA_FETCH_WORKERS","4"))))) as pool:
        for rid,values,meta,errs in pool.map(safe_read,jobs):
            if rid is None:
                errors += errs;continue
            seen.add(rid)
            count += sum(len(v) for v in values.values())
            errors += errs
            previous = old['odds'].get(rid,{})
            if values or meta.get('markets'):
                remember_pre_start(old,race_index.get(rid),rid,values,meta)
                old['odds'][rid] = {**previous,**values}
                prior_meta = old['meta'].get(rid,{})
                meta['sources'] = {**prior_meta.get('sources',{}),**meta['sources']}
                meta['coverage'] = {**prior_meta.get('coverage',{}),**meta['coverage']}
                # A failed/absent market retains its own old observation time;
                # another market's success cannot make that price fresh.
                for typ,state in meta['markets'].items():
                    if state['status']!='success':
                        prior=prior_meta.get('markets',{}).get(typ,{})
                        if prior.get('observedAt'): state['observedAt']=prior['observedAt']
                old['meta'][rid] = meta
    for rid in race_index.keys()-seen:
        prior=old['meta'].setdefault(rid,{})
        prior['markets']={t:{**prior.get('markets',{}).get(t,{}),'status':'error' if errors else 'unpublished','checkedAt':datetime.now(JST).isoformat(timespec='seconds'),'reason':'公式レースページ取得失敗' if errors else '公式オッズリンク未掲載'} for t in ('単勝','複勝',*[v[0] for v in TYPES.values()])}
    with tempfile.NamedTemporaryFile('w',dir=target.parent,delete=False) as f:
        json.dump(old,f,ensure_ascii=False,separators=(',',':'));f.write('\n');temporary=Path(f.name)
    temporary.replace(target)
    print(json.dumps({'phase':'odds','date':date,'races':len(jobs),'values':count,'errors':errors},ensure_ascii=False))

def main():
    try:return run_main()
    except Exception:
        # Index/meeting fetch failure still publishes an explicit failure state;
        # previously observed prices and immutable pre-start history survive.
        target=ROOT/'dist/jra-odds.json'
        data=ROOT/'dist/results-data.js'
        if target.exists() and data.exists():
            old=json.loads(target.read_text());races=json.loads(data.read_text().split('=',1)[1].rstrip(';\n'))['races']
            date=sys.argv[1] if len(sys.argv)>1 else datetime.now(JST).date().isoformat()
            for race in races:
                if race['date']!=date:continue
                meta=old['meta'].setdefault(race['id'],{})
                meta['markets']={t:{**meta.get('markets',{}).get(t,{}),'status':'error','checkedAt':datetime.now(JST).isoformat(timespec='seconds'),'reason':'公式開催・レース索引取得失敗'} for t in ('単勝','複勝',*[v[0] for v in TYPES.values()])}
            temporary=target.with_suffix('.failure-tmp');temporary.write_text(json.dumps(old,ensure_ascii=False,separators=(',',':'))+'\n');temporary.replace(target)
        raise

if __name__=='__main__':
    main()
