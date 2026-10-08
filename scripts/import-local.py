"""Import NAR public snapshots. Preserve missing values; never manufacture predictions.
Raw HTML/CSV caches live outside the deployed tree.
"""
from pathlib import Path
import urllib.request,urllib.parse,csv,json,re,unicodedata,concurrent.futures,importlib.util,time,sys,datetime
ROOT=Path(__file__).resolve().parents[1]; CACHE=ROOT.parent/'nar-cache';CACHE.mkdir(exist_ok=True)
sp=importlib.util.spec_from_file_location('profile_parser',ROOT/'scripts/import-profiles.py');p=importlib.util.module_from_spec(sp);sp.loader.exec_module(p)
BASE='https://www.keiba.go.jp';DATE=sys.argv[1] if len(sys.argv)>1 else datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()
datetime.date.fromisoformat(DATE)
DAY=DATE.replace('-','');YEAR=int(DATE[:4]);MONTH=int(DATE[5:7])
QUICK='--quick' in sys.argv[2:]
CODES={'帯広ば':'3','門別':'36','盛岡':'10','水沢':'11','浦和':'18','船橋':'19','大井':'20','川崎':'21','金沢':'22','笠松':'23','名古屋':'24','園田':'27','姫路':'28','高知':'31','佐賀':'32'}
def get(path,key):
 file=CACHE/(key+'.html')
 current=DATE==datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()
 refresh=current and (key.startswith('nar-'+DATE) or key==f'month-{YEAR}-{MONTH}')
 if file.exists() and not refresh:return file.read_text()
 for attempt in range(2):
  try:
   text=urllib.request.urlopen(BASE+path,timeout=40).read().decode('utf-8');file.write_text(text);return text
  except Exception:
   if attempt:raise
 return ''
def norm(v):return unicodedata.normalize('NFKC',v).strip()
def raceid(v,d,n):return 'nar-'+d+'-'+CODES[v]+'-'+str(int(n))
def rows(path):return list(csv.DictReader(Path(path).read_text(encoding='utf-8-sig').splitlines()))
def month(m):
 try:
  text=get('/KeibaWeb/MonthlyConveneInfo/MonthlyConveneInfoTop?k_year='+str(YEAR)+'&k_month='+str(m),f'month-{YEAR}-{m}');s=p.tree(text)
  table=next(t for t in s.all('table') if '帯広ば' in t.text());dates={}
  for tr in table.all('tr')[2:]:
   cs=p.cells(tr)
   if not cs or cs[0].text() not in CODES:continue
   venue=cs[0].text()
   for day,c in enumerate(cs[1:-1],1):
    if c.text() in ['●','☆','Ｄ','D','△']:
     date=f'{YEAR}-{m:02d}-{day:02d}';dates.setdefault(date,[]).append({'course':venue,'code':CODES[venue],'meeting':venue+'競馬','night':c.text()=='☆'})
  return [{'date':d,'day':'月火水木金土日'[__import__('datetime').date.fromisoformat(d).weekday()]+'曜','courses':v,'graded':[]} for d,v in dates.items()]
 except Exception as e:print('MONTH FAILED',m,str(e),flush=True);return []
# Download the official daily CSVs. Completed historical days are immutable here.
def ensure_csv_snapshot():
 import zipfile,io
 for kind,suffixes in [('Race',['racelist','horselist','payback']),('Odds',['odds'])]:
  if all((CACHE/(DAY+'_'+suffix+'.csv')).exists() for suffix in suffixes) and DATE<datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat():continue
  path=f'/KeibaWeb/DataDownload/{kind}DataDownload?type=daily&k_raceDate={YEAR}%2F{MONTH:02d}%2F{int(DAY[-2:]):02d}'
  archive=zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(BASE+path,timeout=60).read()))
  expected={DAY+'_'+suffix+'.csv' for suffix in suffixes}
  members={Path(name).name:name for name in archive.namelist() if Path(name).name in expected}
  if set(members)!=expected:raise ValueError('NAR archive does not contain '+DATE+' '+kind+' data')
  for base,name in members.items():(CACHE/base).write_bytes(archive.read(name))
ensure_csv_snapshot()
previous=json.loads((ROOT/'dist/local-data.js').read_text().split('=',1)[1].rstrip(';\n'))
previous_horses={h['id']:h for h in previous['horses'] if h['raceId'].startswith('nar-'+DATE+'-')}
previous_races={r['id']:r for r in previous['races'] if r['date']==DATE}
rawr=rows(CACHE/(DAY+'_racelist.csv'));rawh=rows(CACHE/(DAY+'_horselist.csv'));rawp=rows(CACHE/(DAY+'_payback.csv'));rawo=rows(CACHE/(DAY+'_odds.csv'))
races=[];horses=[];odds={};profiles={};errors=[]
for x in rawr:
 v=x['競馬場'];n=x['レース番号'];rid=raceid(v,DATE,n);dist=int(x['距離']);surf=x['芝ダート区分'];start=x['発走時刻'];corners=[]
 for i in range(1,9):
  name=norm(x['コーナー名称'+str(i)]).replace('角','コーナー');order=norm(x['コーナー通過順'+str(i)])
  if name and order:corners.append({'corner':name,'order':order})
 races.append({'id':rid,'organization':'nar','date':DATE,'course':v,'meeting':v+'競馬','number':int(n),'name':x['レース名'],'category':x['条件'],'className':x['競走種類名称'],'grade':next((g for g in ['JpnIII','JpnII','JpnI'] if g in norm(x['レース名'])),''),'surface':surf,'track':surf+'・'+x['回り'],'distance':str(dist),'startTime':start[:2]+':'+start[2:],'time':start[:2]+':'+start[2:],'weather':x['天候'],'going':x['馬場'],'field':int(x['頭数']),'status':'entries','laps':[float(x['ハロンタイム'+str(i)]) for i in range(1,16) if x['ハロンタイム'+str(i)]],'cornerOrders':corners,'payouts':[]})
for x in rawo:
 rid=raceid(x['競馬場'],DATE,x['レース番号']);typ={'馬複':'馬連','枠複':'枠連','三連複':'3連複','三連単':'3連単','３連複':'3連複','３連単':'3連単'}.get(x['賭式'],x['賭式']);pick='-'.join(x['番号'+str(i)] for i in range(1,4) if x['番号'+str(i)])
 if typ in ['枠連','馬連','ワイド','3連複']:pick='-'.join(sorted(pick.split('-'),key=int))
 odds.setdefault(rid,{}).setdefault(typ,{})[pick]=[float(x[k]) for k in ['オッズ','オッズ（最大）'] if x[k] and float(x[k])>0]
for x in rawh:
 rid=raceid(x['競馬場'],DATE,x['レース番号']);n=x['馬番'];t=x['タイム'];secs=int(t)/10 if t.isdigit() else None;tim=(str(int(secs//60))+':'+f'{secs%60:04.1f}') if secs is not None else ''
 h={'id':rid+'-horse-'+n,'raceId':rid,'organization':'nar','number':n,'name':x['馬名'],'frame':int(x['枠番']),'jockey':x['騎手名'],'jockeyAffiliation':x['騎手所属'],'trainer':x['調教師'],'affiliation':x['調教師所属'],'load':x['負担重量'],'weight':x['馬体重'],'weightChange':('+' if x['馬体重増減'].isdigit() and int(x['馬体重増減'])>0 else '')+x['馬体重増減'],'sire':x['父馬名'],'dam':x['母馬名'],'damsire':x['母父馬名'],'owner':x['馬主氏名'],'breeder':x['生産牧場名'],'age':x['性']+x['齢']+'歳 '+x['毛色'],'birthday':x['生年月日'],'finish':x['着順'],'time':tim,'margin':norm(x['着差']).replace('.',' ') if re.fullmatch(r'\d+\.\d/\d',x['着差']) else norm(x['着差']),'last3f':x['上がり3F'],'popularity':x['人気'],'winOdds':str((odds.get(rid,{}).get('単勝',{}).get(n) or [''])[0]),'history':[],'historyComplete':False,'officialRecords':{k:x[k] for k in ['全成績','ダート左成績','ダート右成績','当競馬場成績','うち当距離成績']},'passing':''}
 horses.append(h)
for x in rawp:
 rid=raceid(x['競馬場'],DATE,x['レース番号']);r=next((r for r in races if r['id']==rid),None)
 if not r:continue
 def pay(typ,nums,amount,pop):
  vals=[x.get(k,'') for k in nums];a=x.get(amount,'')
  if all(vals) and a.isdigit():
   if typ in ['枠連','馬連','ワイド','3連複']:vals.sort(key=int)
   r['payouts'].append({'type':typ,'pick':'-'.join(vals),'amount':int(a),'popularity':x.get(pop,'')})
 pay('単勝',['単勝組番'],'単勝払戻金（円）','単勝人気')
 for i in range(1,4):pay('複勝',['複勝組番'+str(i)],f'複勝払戻金{i}（円）',f'複勝人気{i}');pay('ワイド',[f'ワイド組番{i}馬番1',f'ワイド組番{i}馬番2'],f'ワイド払戻金{i}（円）',f'ワイド人気{i}')
 for typ,prefix,pop in [('枠連','枠複','枠複人気'),('馬連','馬複','馬複人気1'),('馬単','馬単','馬単人気1')]:pay(typ,[prefix+'組番1',prefix+'組番2'],prefix+'払戻金（円）',pop)
 for typ,prefix in [('3連複','３連複'),('3連単','３連単')]:pay(typ,[prefix+'組番馬番'+str(i) for i in range(1,4)],prefix+'払戻金（円）',prefix+'人気')
def result(r):
 if QUICK and previous_races.get(r['id'],{}).get('status')=='results':
  for h in horses:
   old=previous_horses.get(h['id'])
   if h['raceId']==r['id'] and old and old['name']==h['name']:
    for key in ['passing','isRecord','lineageCode']:
     if key in old:h[key]=old[key]
    if old.get('lineageCode'):profiles[h['id']]=old['lineageCode']
  return True
 try:
  path=f'/KeibaWeb/TodayRaceInfo/RaceMarkTable?k_raceDate={YEAR}%2F{MONTH:02d}%2F{int(DAY[-2:]):02d}&k_raceNo={r["number"]}&k_babaCode={CODES[r["course"]]}'
  s=p.tree(get(path,r['id']));table=s.all('table')[0]
  for tr in table.all('tr')[1:]:
   cs=p.cells(tr)
   if len(cs)<16:continue
   n=cs[2].text();h=next((h for h in horses if h['raceId']==r['id'] and h['number']==n),None)
   if not h:continue
   h['finish']=norm(cs[0].text()) or ('中止' if norm(cs[11].text())=='中止' else '');h['passing']=norm(cs[13].text());h['isRecord']='レコード' in cs[10].text() or 'レコード' in cs[11].text()
   a=next((a for a in cs[3].all('a') if 'HorseMarkInfo' in a.a.get('href','')),None)
   if a:
    code=urllib.parse.parse_qs(urllib.parse.urlparse(a.a['href']).query)['k_lineageLoginCode'][0];profiles[h['id']]=code;h['lineageCode']=code
  return True
 except Exception as e:errors.append({'race':r['id'],'error':str(e)});return False
if QUICK and previous.get('year')==YEAR and previous['schedule']:
 schedule=[d for d in previous['schedule'] if int(d['date'][:4])==YEAR]
else:
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  schedule=[d for ds in pool.map(month,range(1,13)) for d in ds]
print('CALENDAR',len(schedule),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 for i,ok in enumerate(pool.map(result,races),1):
  if i%10==0:print('RESULT',i,'/',len(races),flush=True)
print('PROFILES',len(profiles),flush=True)
for r in races:
 if r['payouts'] or any(h['finish'].isdigit() for h in horses if h['raceId']==r['id']):r['status']='results'
 if previous_races.get(r['id'],{}).get('status')=='results' and r['status']!='results':
  r.update(previous_races[r['id']])
  for h in horses:
   old=previous_horses.get(h['id'])
   if h['raceId']==r['id'] and old and old['name']==h['name']:h.update(old)
def history(h):
 old=previous_horses.get(h['id'])
 if QUICK and old and old['name']==h['name'] and old.get('historyComplete'):
  h['history']=old['history'];h['historyComplete']=True
  if old.get('lineageCode'):h['lineageCode']=old['lineageCode']
  if 'isRecord' in old and 'isRecord' not in h:h['isRecord']=old['isRecord']
  return
 code=profiles.get(h['id'])
 if not code:return
 try:
  s=p.tree(get('/KeibaWeb/DataRoom/HorseMarkInfo?k_lineageLoginCode='+code,'horse-'+code));assert norm(s.first('h4','odd_title').text())==norm(h['name']);table=s.first('table','HorseMarkInfo_table');out=[]
  for tr in table.all('tr')[1:]:
   cs=p.cells(tr);vs=[norm(c.text()) for c in cs]
   if len(vs)<22 or not re.fullmatch(r'\d{4}/\d{2}/\d{2}',vs[0]):continue
   d=vs[0].replace('/','-')
   if d>=DATE:continue
   # Weather/going and optional night flag precede the field-size cell.
   fi=next(i for i,c in enumerate(cs) if c.a.get('class')=='dbdata2');field=vs[fi];base=fi
   distance=vs[5];surface='障害' if '障' in distance else '芝' if '芝' in distance else 'ダート';venue=vs[1].replace('J','').strip();distance=re.sub(r'\D','',distance)
   out.append({'date':d,'course':venue,'name':vs[3],'grade':vs[4],'distance':distance,'surface':surface,'going':vs[7],'field':field,'finish':vs[base+4],'time':vs[base+5],'margin':vs[base+6],'last3f':vs[base+7],'weight':vs[base+8],'jockey':re.sub(r'\s*\(.*','',vs[base+9]),'load':vs[base+10],'passing':''})
  h['history']=out;h['historyComplete']=bool(table.all('tr')) or ('指定の馬の出走履歴がありません' in s.text() and h['officialRecords']['全成績']=='0-0-0-0')
 except Exception as e:errors.append({'horse':h['id'],'error':str(e)})
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 for i,_ in enumerate(pool.map(history,horses),1):
  if i%50==0:print('HISTORY',i,'/',len(horses),flush=True)
past_races=[r for r in previous['races'] if r['date']!=DATE]
old_ids={r['id'] for r in previous['races'] if r['date']==DATE}
past_horses=[h for h in previous['horses'] if h['raceId'] not in old_ids]
old_schedule=[d for d in previous['schedule'] if int(d['date'][:4])!=YEAR]
data={'year':YEAR,'snapshotDate':DATE,'fetchedAt':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M JST'),'source':'地方競馬全国協会（NAR）公式・公開CSV／競走馬情報','schedule':sorted(old_schedule+schedule,key=lambda d:d['date']),'races':past_races+races,'horses':past_horses+horses,'errors':errors}
old_odds=json.loads((ROOT/'dist/local-odds-data.js').read_text().split('Object.assign(ODDS_DATA,',1)[1].rstrip(');\n'))
for rid in {r['id'] for r in races}:old_odds.pop(rid,None)
old_odds.update(odds)
if all(data.get(k)==previous.get(k) for k in ['year','snapshotDate','source','schedule','races','horses','errors']) and old_odds==json.loads((ROOT/'dist/local-odds-data.js').read_text().split('Object.assign(ODDS_DATA,',1)[1].rstrip(');\n')):
 data['fetchedAt']=previous['fetchedAt']
else:
 (ROOT/'dist/local-data.js').write_text('const LOCAL_DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';\n')
 (ROOT/'dist/local-odds-data.js').write_text('Object.assign(ODDS_DATA,'+json.dumps(old_odds,ensure_ascii=False,separators=(',',':'))+');\n')
print('DONE',len(races),len(horses),sum(len(h['history']) for h in horses),'history rows',len(errors),'errors',flush=True)
