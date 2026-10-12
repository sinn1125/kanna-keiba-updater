"""Build time-causal, offline ML predictions from public JRA snapshots.
No race-day result, popularity, odds, workout, or later opponent result is a feature.
Raw parsed caches and training arrays remain outside the published tree.
"""
from pathlib import Path
from collections import defaultdict,Counter
import re,json,math,bisect,unicodedata
import numpy as np
from lxml import html
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT.parent/'profile-cache'
VENUES=['札幌','函館','福島','新潟','東京','中山','中京','京都','阪神','小倉']
META={ # A-course straight length and elevation, JRA official course pages 2026/10/06.
 '札幌':{'direction':'右','芝':(266.1,.7),'ダート':(264.3,.9)},
 '函館':{'direction':'右','芝':(262.1,3.5),'ダート':(260.3,3.5)},
 '福島':{'direction':'右','芝':(292,1.9),'ダート':(295.7,2.1)},
 '新潟':{'direction':'左','芝':(358.7,.8),'外':(658.7,2.2),'ダート':(353.9,.6)},
 '東京':{'direction':'左','芝':(525.9,2.7),'ダート':(501.6,2.5)},
 '中山':{'direction':'右','芝':(310,5.3),'外':(310,5.3),'ダート':(308,4.5)},
 '中京':{'direction':'左','芝':(412.5,3.5),'ダート':(410.7,3.4)},
 '京都':{'direction':'右','芝':(328.4,3.1),'外':(403.7,4.3),'ダート':(329.1,3)},
 '阪神':{'direction':'右','芝':(356.5,1.9),'外':(473.6,2.4),'ダート':(352.7,1.6)},
 '小倉':{'direction':'右','芝':(293,3),'ダート':(291.3,2.9)}}
def norm(s):return re.sub(r'\s','',unicodedata.normalize('NFKC',s))
def text(el):return ' '.join(el.itertext()).strip() if el is not None else ''
def one(el,cls):return next(iter(el.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," '+cls+' ")]')),None)
def sec(s):
 m=re.fullmatch(r'(?:(\d+):)?(\d+(?:\.\d+)?)',s);return int(m[1] or 0)*60+float(m[2]) if m else None

def grade(g):
 g=unicodedata.normalize('NFKC',g).replace(' ','')
 for label,v in [('GIII',6),('GII',7),('GI',8),('G3',6),('G2',7),('G1',8),('リステッド',5),('オープン',5),('3勝',4),('2勝',3),('1勝',2),('未勝利',1),('新馬',0)]:
  if label in g:return v
 return 1

def parse():
 snapshot=CACHE/'ai-races.json'
 if snapshot.exists():return json.loads(snapshot.read_text())
 races={}; files=sorted(set(CACHE.glob('results-pw01ses1*.html'))|set(CACHE.glob('history-results-pw01ses1*.html')))
 for i,file in enumerate(files):
  m=re.search(r'pw01ses10(\d{2})(\d{4})(\d{2})(\d{2})(\d{8})',file.name)
  if not m:continue
  venue=VENUES[int(m[1])-1];raw=m[5];date=f'{raw[:4]}-{raw[4:6]}-{raw[6:]}'
  root=html.fromstring(file.read_text())
  for unit in root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," race_result_unit ")]'):
   number=int(re.search(r'_(\d+)R',unit.get('id'))[1]);key=f'{date}|{venue}|{number}'
   course=text(one(unit,'course'));distance=int(re.search(r'([\d,]+)\s*メートル',course)[1].replace(',',''))
   cat=text(one(unit,'category'))+' '+text(one(unit,'class'))+' '+text(one(unit,'race_name'))
   route=re.search(r'（([^）]+)）',course);route=route[1] if route else ''
   surface='障害' if '障害' in cat or '→' in route or '芝ダート' in route else 'ダート' if 'ダート' in route else '芝'
   images=unit.xpath('.//div[contains(@class,"race_header")]//img[contains(@src,"grade")]/@alt')
   race={'id':key,'date':date,'course':venue,'number':number,'name':text(one(unit,'race_name')),'distance':distance,'surface':surface,'route':route,'grade':grade(' '.join(images)+' '+cat),'runners':[]}
   for row in unit.xpath('.//tbody/tr'):
    cs={c.get('class',''):c for c in row.xpath('./td')};finish=text(cs.get('place'))
    if not cs or any(x in finish for x in ['取消','除外']):continue
    hnode=cs.get('horse')
    if hnode is None:continue
    name=text(hnode);cn=' '.join(hnode.xpath('.//a/@href'));hid=re.search(r'pw01dud\d(\d{10})',cn)
    if not hid:continue
    pos=[int(text(x)) for x in cs.get('corner',html.Element('div')).xpath('.//li') if text(x).isdigit()]
    race['runners'].append({'horseId':hid[1],'name':norm(name),'number':int(text(cs.get('num'))),'finish':int(finish) if finish.isdigit() else None,'time':sec(text(cs.get('time'))),'last3f':sec(text(cs.get('f_time'))),'passing':pos})
   race['field']=len(race['runners']);winner=next((h['time'] for h in race['runners'] if h['finish']==1),None)
   fts=sorted(h['last3f'] for h in race['runners'] if h['last3f'] is not None)
   for h in race['runners']:
    h['raceId']=key;h['date']=date;h['margin']=max(0,h['time']-winner) if h['time'] is not None and winner is not None else None
    h['lateRank']=bisect.bisect_left(fts,h['last3f'])/(len(fts)-1) if h['last3f'] is not None and len(fts)>1 else None
   races[key]=race
  if i%200==0:print('Parsed JRA days',i,'/',len(files),flush=True)
 snapshot.write_text(json.dumps(races,ensure_ascii=False,separators=(',',':')));return races
RACES=parse();HISTORY=defaultdict(list)
for r in RACES.values():
 for h in r['runners']:
  # Imported snapshots can contain the same URL ID for different names.
  # Never merge those records: require both the identifier and normalized name.
  h['horseId']=h['horseId']+'|'+h['name']
  HISTORY[h['horseId']].append(h)
for hs in HISTORY.values():
 hs.sort(key=lambda h:(h['date'],h['raceId']))
 for i,h in enumerate(hs):h['next']=hs[i+1] if i+1<len(hs) else None
print('Indexed',len(RACES),'races',sum(map(len,HISTORY.values())),'starts',flush=True)

def past(hid,date):return [h for h in HISTORY[hid] if h['date']<date]
def course_meta(r):
 if r['surface']=='障害':return None
 if r['course']=='新潟' and r['surface']=='芝' and r['distance']==1000:return {'direction':'直線','straight':1000,'elevation':.1,'layout':'直線'}
 layout='外' if '外' in r['route'] and '内' not in r['route'] else '内' if '内' in r['route'] else '通常'
 meta=META[r['course']];st,el=meta.get('外' if layout=='外' and r['surface']=='芝' else r['surface'],meta[r['surface']])
 return {'direction':meta['direction'],'straight':st,'elevation':el,'layout':layout}
def similar(a,b):
 if a['surface']!=b['surface'] or abs(a['distance']-b['distance'])>200 or a['course']==b['course']:return False
 x,y=course_meta(a),course_meta(b)
 return bool(x and y and x['direction']==y['direction'] and abs(x['straight']-y['straight'])<=150 and abs(x['elevation']-y['elevation'])<=2.5)
def perf(h):
 r=RACES[h['raceId']];rank=1-(h['finish']-1)/max(1,r['field']-1) if h['finish'] else 0
 return .6*rank+.25*(math.exp(-h['margin']) if h['margin'] is not None else rank)+.15*(1-h['lateRank'] if h['lateRank'] is not None else rank)
def weighted(hs,default=.5,weights=None):
 if not hs:return default
 weights=weights or [0.85**i for i in range(len(hs))];return sum(perf(h)*w for h,w in zip(hs,weights))/sum(weights)
def style(hs,surface):
 samples=[]
 for h in hs[:5]:
  if RACES[h['raceId']]['surface']!=surface or not h['passing']:continue
  pos=h['passing'][0];field=RACES[h['raceId']]['field']
  if pos<1 or pos>field:continue
  samples.append(0 if pos==1 else 1 if pos<=math.ceil(field/3) else 2 if pos<=math.ceil(field*2/3) else 3)
 if not samples:return None,0
 counts=Counter(samples);return next(i for i in samples if counts[i]==max(counts.values())),len(samples)
def rivals(run,hid,cutoff):
 r=RACES[run['raceId']];out=[]
 for h in r['runners']:
  if h['horseId']==hid or not h['finish'] or h['finish']>5:continue
  n=h['next'];known=bool(n and n['date']<cutoff)
  out.append({'name':h['name'],'finish':h['finish'],'next':({'date':n['date'],'course':RACES[n['raceId']]['course'],'name':RACES[n['raceId']]['name'],'finish':n['finish'],'grade':RACES[n['raceId']]['grade']} if known else None)})
 return sorted(out,key=lambda h:h['finish'])
FEATURES=['近5走内容','相手次走好走率','内容と相手の組合せ','クラス差','距離適性','距離実績数','コース適性','コース実績数','類似コース使用','逃げ','先行','差し','追込','逃げ候補割合','前方候補割合','脚質と逃げ候補','脚質と前方候補','脚質と直線長','脚質不明','頭数']
def analyse(hid,r,styles,evidence=False):
 hs=list(reversed(past(hid,r['date'])));recent=hs[:5];same_surface=[h for h in hs if RACES[h['raceId']]['surface']==r['surface']]
 pairs=[rivals(h,hid,r['date']) for h in recent];known=[x for row in pairs for x in row if x['next']]
 # Beta(1,2) shrinks incomplete next-start evidence towards 1/3; unknown never becomes a defeat.
 level=(sum(x['next']['finish'] is not None and x['next']['finish']<=3 for x in known)+1)/(len(known)+3)
 exact_distance=[h for h in same_surface if RACES[h['raceId']]['distance']==r['distance']]
 near_distance=[h for h in same_surface if abs(RACES[h['raceId']]['distance']-r['distance'])<=200]
 dist=exact_distance or near_distance
 target_meta=course_meta(r)
 exact_course=[h for h in same_surface if RACES[h['raceId']]['course']==r['course'] and abs(RACES[h['raceId']]['distance']-r['distance'])<=200 and (course_meta(RACES[h['raceId']])==None or target_meta==None or course_meta(RACES[h['raceId']])['layout']==target_meta['layout'])]
 near_course=[h for h in same_surface if similar(RACES[h['raceId']],r)] if not exact_course else []
 courses=exact_course or near_course
 def shrink(rows):return (weighted(rows)*len(rows)+.5*2)/(len(rows)+2)
 s,ns=styles.get(hid,(None,0));n=max(1,len(styles));escape=sum(x[0]==0 for x in styles.values())/n;front=sum(x[0] in [0,1] for x in styles.values())/n;si=(s/3 if s is not None else .5)
 pp=weighted(recent);x=[pp,level,pp*level,(sum(RACES[h['raceId']]['grade'] for h in recent)/len(recent)-r['grade'])/8 if recent else 0,shrink(dist),min(len(dist),10)/10,shrink(courses),min(len(courses),10)/10,int(bool(near_course)),*[int(s==k) for k in range(4)],escape,front,si*escape,si*front,si*(target_meta['straight']/700 if target_meta else .5),int(s is None),r['field']/18]
 if not evidence:return x
 def summary(rows):return {'starts':len(rows),'wins':sum(h['finish']==1 for h in rows),'places':sum(h['finish'] in [1,2] for h in rows),'examples':[{'date':h['date'],'course':RACES[h['raceId']]['course'],'route':RACES[h['raceId']]['route'],'distance':RACES[h['raceId']]['distance'],'finish':h['finish'],'margin':h['margin'],'last3f':h['last3f']} for h in rows[:5]]}
 ev={'recent':[{'date':h['date'],'course':RACES[h['raceId']]['course'],'name':RACES[h['raceId']]['name'],'finish':h['finish'],'field':RACES[h['raceId']]['field'],'margin':h['margin'],'last3f':h['last3f'],'rivals':p} for h,p in zip(recent,pairs)],'distance':summary(dist),'distanceKind':'同距離' if exact_distance else '近距離（±200m）','course':summary(courses),'courseKind':'同競馬場・同走路' if exact_course else '類似コース' if near_course else '根拠なし','style':['逃げ','先行','差し','追込'][s] if s is not None else '未判定','styleSamples':ns,'escapeCount':sum(v[0]==0 for v in styles.values()),'frontCount':sum(v[0] in [0,1] for v in styles.values()),'unknownCount':sum(v[0] is None for v in styles.values()),'meta':target_meta,'recentCount':len(recent),'rivalKnown':len(known),'rivalTotal':sum(map(len,pairs)),'rivalPodiums':sum(x['next']['finish'] is not None and x['next']['finish']<=3 for x in known)}
 return x,ev

# Every training example's inputs are reconstructed strictly before that race date.
train={s:[] for s in ['平地','障害']};valid={s:[] for s in train}
for i,r in enumerate(sorted(RACES.values(),key=lambda r:r['id'])):
 if not '2025-01-01'<=r['date']<'2026-10-04' or r['field']<3:continue
 group='障害' if r['surface']=='障害' else '平地'
 styles={h['horseId']:style(list(reversed(past(h['horseId'],r['date']))),r['surface']) for h in r['runners']}
 if sum(bool(past(h['horseId'],r['date'])) for h in r['runners'])<r['field']*.5:continue
 bucket=train[group] if r['date']<'2026-01-01' else valid[group]
 for h in r['runners']:
  if not past(h['horseId'],r['date']):continue
  bucket.append((analyse(h['horseId'],r,styles),int(h['finish']==1),int(h['finish'] in [1,2]),r['id'],h['horseId'],int(h['finish'] in [1,2,3])))
 if i%1000==0:print('Built causal features',i,flush=True)
models={};model_info={}
for group,rows in train.items():
 X=np.array([r[0] for r in rows]);scale=StandardScaler().fit(X);Z=scale.transform(X)
 models[group]=(scale,LogisticRegression(C=.2,max_iter=500,random_state=7).fit(Z,[r[1] for r in rows]),LogisticRegression(C=.2,max_iter=500,random_state=7).fit(Z,[r[2] for r in rows]),LogisticRegression(C=.2,max_iter=500,random_state=7).fit(Z,[r[5] for r in rows]))
 sc,m1,m2,m3=models[group];test=valid[group];V=sc.transform(np.array([r[0] for r in test]));pw=m1.predict_proba(V)[:,1];pl=m2.predict_proba(V)[:,1];pp=m3.predict_proba(V)[:,1];by=defaultdict(list)
 for row,w,l,p in zip(test,pw,pl,pp):by[row[3]].append((row,float(w),float(l),float(p)))
 hits1=sum(max(rr,key=lambda a:a[1])[0][1] for rr in by.values());hits2=sum(max(rr,key=lambda a:a[2])[0][2] for rr in by.values());hits3=sum(max(rr,key=lambda a:a[3])[0][5] for rr in by.values())
 model_info[group]={'trainingStarts':len(rows),'trainingRaces':len(set(r[3] for r in rows)),'validationStarts':len(test),'validationRaces':len(by),'winFirstHits':hits1,'placeFirstHits':hits2,'podiumFirstHits':hits3,'mean':sc.mean_.tolist(),'scale':sc.scale_.tolist(),'winCoefficients':m1.coef_[0].tolist(),'winIntercept':float(m1.intercept_[0]),'placeCoefficients':m2.coef_[0].tolist(),'placeIntercept':float(m2.intercept_[0]),'podiumCoefficients':m3.coef_[0].tolist(),'podiumIntercept':float(m3.intercept_[0])}
 print(group,'train',len(rows),'validation races',len(by),'top win',hits1,'top place',hits2,'top three',hits3,flush=True)

source=json.loads((ROOT/'dist/results-data.js').read_text().split('=',1)[1].rstrip(';\n'));predictions={};checks=[]
for target in source['races']:
 rid=f"{target['date']}|{target['course']}|{target['number']}";r=RACES[rid];styles={h['horseId']:style(list(reversed(past(h['horseId'],r['date']))),r['surface']) for h in r['runners']};group='障害' if r['surface']=='障害' else '平地';scale,m1,m2,m3=models[group];entries=[h for h in source['horses'] if h['raceId']==target['id']];items={}
 for h in entries:
  hid=re.search(r'pw01dud\d(\d{10})',h['horseCname'])[1]+'|'+norm(h['name'])
  assert h['finish'] in ['取消','除外'] or any(x['horseId']==hid and x['name']==norm(h['name']) for x in r['runners']),h['name']
  # Verify the model sees every one of the requested latest five central starts.
  latest=sorted([x for x in h['history'] if not any(z in str(x['finish']) for z in ['取消','除外'])],key=lambda x:x['date'],reverse=True)[:5]
  X,ev=analyse(hid,r,styles,True);assert [x['date'] for x in ev['recent']]==[x['date'] for x in latest],(h['name'],ev['recent'],latest)
  eligible=ev['recentCount']>0 and h['finish'] not in ['取消','除外'];z=scale.transform([X]);items[h['id']]={'mark':'','winScore':float(m1.predict_proba(z)[0,1]) if eligible else None,'placeScore':float(m2.predict_proba(z)[0,1]) if eligible else None,'podiumScore':float(m3.predict_proba(z)[0,1]) if eligible else None,'evidence':ev,'features':X,'number':int(h['number'])}
 available=[(hid,x) for hid,x in items.items() if x['winScore'] is not None];min_known=max(2,math.ceil(len(entries)/2));ranked=[]
 if len(available)>=min_known:
  first=max(available,key=lambda z:(z[1]['winScore'],-z[1]['number']));first[1]['mark']='◎';ranked=[first[0]]
  rest=sorted([z for z in available if z[0]!=first[0]],key=lambda z:(-z[1]['placeScore'],z[1]['number']))
  for (hid,x),mark in zip(rest[:4],['○','▲','△','△']):x['mark']=mark;ranked.append(hid)
 predictions[target['id']]={'asOf':r['date'],'group':group,'status':'predicted' if ranked else 'insufficient','unknownHorses':len(entries)-len(available),'items':items,'ranked':ranked}
 for hid,x in items.items():
  for run in x['evidence']['recent']:
   assert run['date']<r['date']
   for opponent in run['rivals']:
    if opponent['next']:assert run['date']<opponent['next']['date']<r['date']
output={'version':'ml-three-factors-top3-v1','fetchedAt':'2026-10-07','trainingPeriod':'2025-01-01〜2025-12-31','validationPeriod':'2026-01-01〜2026-10-03','historySource':'JRA公式レース結果・競走馬情報（中央競馬）','courseSource':'JRA公式10場コース紹介（Aコース数値）','features':FEATURES,'models':model_info,'races':predictions}
(ROOT/'dist/ai-predictions-data.js').write_text('const AI_PREDICTIONS='+json.dumps(output,ensure_ascii=False,separators=(',',':'))+';\n')
print('Exported',len(predictions),'races',sum(len(r['items']) for r in predictions.values()),'horses',flush=True)
