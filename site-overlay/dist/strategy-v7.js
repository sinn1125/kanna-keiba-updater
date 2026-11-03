/* Ver.7 development candidate. No purchase, no production adoption implied. */
(function(root){
'use strict';
const VERSION='v7-strategy-candidate-1';
const TYPES=['単勝','複勝','馬連','ワイド','馬単','3連複','3連単','枠連','枠単'];
const names=['◎単勝重視','◎複勝重視','単勝・複勝併用','馬連本線','馬連分散','ワイド本線','ワイド分散','馬単本線','馬単複数相手','三連複少点数','三連複1頭軸','三連複フォーメーション','三連単少点数','三連単1着固定','三連単フォーメーション','枠連重視','枠単重視','堅実決着型','混戦対応型','波乱対応型','逃げ有利型','先行有利型','差し有利型','追込有利型','内枠有利型','外枠有利型','重馬場適性型','追い切り良化型','オッズ妙味型','見送り型'];
const definitions=names.map((name,i)=>({id:i+1,name,version:VERSION}));
const clone=x=>JSON.parse(JSON.stringify(x));
const canonical=x=>Array.isArray(x)?x.map(canonical):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,canonical(x[k])])):x;
const signature=x=>JSON.stringify(canonical(x));
const key=(type,ns)=>(['馬単','3連単','枠単'].includes(type)?ns:[...ns].sort((a,b)=>a-b)).join('-');
const validTime=x=>typeof x==='string'&&Number.isFinite(Date.parse(x));
function beforeStart(s,now){return s.race.status==='entries'&&validTime(s.race.startAt)&&Date.parse(now)<Date.parse(s.race.startAt)}
function distributions(runners,placeSlots){
 const markets=Object.fromEntries(TYPES.map(t=>[t,{}])),outcomes=[];
 const add=(type,ns,p)=>{const k=key(type,ns);markets[type][k]=(markets[type][k]||0)+p};
 const total=runners.reduce((a,b)=>a+b.weight,0);
 for(const a of runners)for(const b of runners){if(a.id===b.id)continue;
  const ab=a.weight/total*b.weight/(total-a.weight);
  add('馬連',[a.number,b.number],ab);add('馬単',[a.number,b.number],ab);
  if(a.frame&&b.frame){add('枠連',[a.frame,b.frame],ab);add('枠単',[a.frame,b.frame],ab)}
  for(const c of runners){if(c.id===a.id||c.id===b.id)continue;
   const p=ab*c.weight/(total-a.weight-b.weight),podium=[a,b,c],places=podium.slice(0,placeSlots);
   outcomes.push({order:podium.map(h=>h.number),frames:podium.map(h=>h.frame),p});
   add('単勝',[a.number],p);
   for(const h of places)add('複勝',[h.number],p);
   for(let i=0;i<3;i++)for(let j=i+1;j<3;j++)add('ワイド',[podium[i].number,podium[j].number],p);
   add('3連複',podium.map(h=>h.number),p);add('3連単',podium.map(h=>h.number),p);
  }
 }
 return {markets,outcomes};
}
function isHit(ticket,outcome,placeSlots){
 const n=outcome.order,ns=ticket.pick.split('-').map(Number);
 if(ticket.type==='単勝')return ns[0]===n[0];
 if(ticket.type==='複勝')return n.slice(0,placeSlots).includes(ns[0]);
 if(ticket.type==='ワイド')return ns.every(x=>n.includes(x));
 const count=ticket.type.includes('3連')?3:2,seq=ticket.type.includes('枠')?outcome.frames.slice(0,2):n.slice(0,count);
 return key(ticket.type,seq)===ticket.pick;
}
function eligibleSnapshot(s,now){
 const missing=[];
 if(!beforeStart(s,now))missing.push('発走前の入力ではありません');
 if(s.forecast.kind!=='pre-race'||!validTime(s.forecast.generatedAt)||Date.parse(s.forecast.generatedAt)>=Date.parse(s.race.startAt)||Date.parse(s.forecast.generatedAt)>Date.parse(now))missing.push('発走前に保存した予想がありません');
 if(s.forecast.status!=='predicted')missing.push('能力予想が保留です');
 if(!s.model?.calibrated)missing.push('全頭の確率校正が未検証です');
 const active=s.runners.filter(h=>!h.withdrawn);
 if(active.length<5||new Set(active.map(h=>h.id)).size!==active.length||new Set(active.map(h=>h.number)).size!==active.length||active.some(h=>!Number.isInteger(h.number)||h.number<1||h.number>18||!(h.weight>0)||!Number.isFinite(h.weight)))missing.push('全出走馬の識別・能力データ不足');
 if(s.race.placeSlots!==2&&s.race.placeSlots!==3)missing.push('複勝の対象着順未確認');
 // Reject acquisition timestamps after decision time, including per-horse history.
 for(const [name,t] of Object.entries(s.sources||{}))if(t&&(!validTime(t)||Date.parse(t)>Date.parse(now)))missing.push(name+'の取得時刻が不正です');
 for(const h of active)if((h.historyDates||[]).some(d=>d>=s.race.date))missing.push('未来の戦績が混入しています');
 return [...new Set(missing)];
}
function candidateTickets(s,dist,now,config){
 const active=s.runners.filter(h=>!h.withdrawn),byNumber=new Map(active.map(h=>[h.number,h]));
 const out=[];
 for(const type of TYPES){
  // JRA does not sell frame exacta. Modelled probabilities do not imply sale.
  if(!s.race.availableTypes.includes(type)||!s.model.validatedTypes?.includes(type))continue;
  const market=s.odds?.[type];if(!market||!validTime(market.asOf)||Date.parse(market.asOf)>=Date.parse(s.race.startAt)||Date.parse(market.asOf)>Date.parse(now)||Date.parse(now)-Date.parse(market.asOf)>config.oddsMaxAgeMs)continue;
  for(const [pick,probability] of Object.entries(dist.markets[type])){
   const odds=market.values?.[pick];if(!Array.isArray(odds)||!odds.length||odds.length>2||odds.some(v=>!Number.isFinite(v)||v<1)||odds[0]>odds.at(-1))continue;
   const expected=probability*odds[0],lower=expected*(1-config.probabilityMargin);
   if(lower<=config.minExpectedReturn)continue;
   out.push({type,pick,probability,odds:[...odds],expectedReturn:expected,conservativeReturn:lower,asOf:market.asOf,
    horses:pick.split('-').map(Number).map(n=>byNumber.get(n)?.id).filter(Boolean)});
  }
 }
 return out.sort((a,b)=>b.conservativeReturn-a.conservativeReturn||b.probability-a.probability||a.type.localeCompare(b.type)||a.pick.localeCompare(b.pick));
}
function evaluate(s,now,options={}){
 const config={oddsMaxAgeMs:1800000,probabilityMargin:.1,minExpectedReturn:1.05,maxPoints:12,...options};
 const missing=eligibleSnapshot(s,now),active=s.runners.filter(h=>!h.withdrawn).sort((a,b)=>(b.weight||0)-(a.weight||0)||a.number-b.number);
 const scored=active.filter(h=>Number.isFinite(h.weight)&&h.weight>0);
 const marks=Object.fromEntries(scored.map((h,i)=>[h.id,i===0?'◎':i===1?'○':i===2?'▲':i<6?'△':'']));
 const base={version:VERSION,modelVersion:s.model?.version||null,generatedAt:now,kind:s.forecast.kind==='pre-race'?'pre-race':'unverified',marks,missing,sourceTimes:clone(s.sources||{}),workouts:clone(s.workouts||{}),amount:null,
  confidence:s.model?.calibrated?'校正済み確率による近似・戦略は未検証':'データ不足',probabilities:null};
 if(missing.length)return {...base,state:'skip',strategies:[30],picks:[],reason:missing.join(' ／ '),comparisons:definitions.map(d=>({...d,state:'unavailable',reason:missing.join(' ／ ')}))};
 const d=distributions(active,s.race.placeSlots),top=active[0],second=active[1],axis=top.number,total=active.reduce((n,h)=>n+h.weight,0),win=top.weight/total,podium=d.markets['複勝'][String(axis)],gap=top.weight/second.weight;
 base.distributionInputs={runners:active.map(({id,number,frame,weight})=>({id,number,frame,weight})),placeSlots:s.race.placeSlots};
 base.probabilities=Object.fromEntries(active.map(h=>[h.id,{win:h.weight/total,place:d.markets['馬連']?Object.entries(d.markets['馬単']).reduce((n,[k,p])=>n+(k.split('-').includes(String(h.number))?p:0),0):null,podium:d.markets['複勝'][String(h.number)]}]));
 const pool=candidateTickets(s,d,now,config),top3=active.slice(0,3).map(h=>h.number),top6=active.slice(0,6).map(h=>h.number);
 const axisPick=t=>t.type.includes('枠')?t.pick.split('-').map(Number).includes(top.frame):t.pick.split('-').map(Number).includes(axis);
 const types=(...ts)=>pool.filter(t=>ts.includes(t.type));
 const evidence=s.context||{},validated=(field,value)=>evidence[field]?.validated===true&&evidence[field]?.value===value;
 const special=(field,value)=>TYPES.filter(t=>!['枠連','枠単'].includes(t)).flatMap(t=>types(t)).filter(t=>t.horses.some(id=>active.find(h=>h.id===id)?.[field]===value));
 const specs=[
 [win>=.3,()=>types('単勝').filter(t=>t.pick===String(axis)),1,'◎の勝率と単勝期待値'],
 [podium>=.55,()=>types('複勝').filter(t=>t.pick===String(axis)),1,'◎の3着内安定性'],
 [win>=.25&&podium>=.55,()=>types('単勝','複勝').filter(axisPick),2,'勝利・3着内の両方を比較'],
 [gap>=1.3,()=>types('馬連').filter(axisPick),1,'◎と相手1頭'],
 [gap>=1.15,()=>types('馬連').filter(axisPick),3,'◎と複数の連対候補'],
 [podium>=.5,()=>types('ワイド').filter(axisPick),1,'上位2頭の3着内期待'],
 [podium>=.45,()=>types('ワイド').filter(axisPick),3,'相手の着順分散'],
 [win>=.3,()=>types('馬単').filter(t=>t.pick.startsWith(axis+'-')),1,'◎の1着を重視'],
 [win>=.25,()=>types('馬単').filter(t=>t.pick.startsWith(axis+'-')),3,'◎1着と2着候補の分散'],
 [top3.reduce((n,x)=>n+d.markets['単勝'][x],0)>=.65,()=>types('3連複').filter(t=>t.pick.split('-').every(x=>top3.includes(+x))),1,'上位3頭'],
 [podium>=.55,()=>types('3連複').filter(axisPick),6,'◎の3着内と相手候補'],
 [gap>=1.15,()=>types('3連複').filter(t=>axisPick(t)&&t.pick.split('-').every(x=>top6.includes(+x))),9,'上位と追加候補を比較'],
 [gap>=1.5,()=>types('3連単').filter(t=>t.pick.split('-').every(x=>top3.includes(+x))),3,'上位の着順序列'],
 [win>=.4,()=>types('3連単').filter(t=>t.pick.startsWith(axis+'-')),6,'◎の1着固定'],
 [gap>=1.2,()=>types('3連単').filter(t=>t.pick.split('-').every(x=>top6.includes(+x))),9,'着順別候補'],
 [true,()=>types('枠連'),3,'枠組合せの期待値'],
 [s.race.availableTypes.includes('枠単'),()=>types('枠単'),3,'発売対象と枠序列'],
 [gap>=1.6,()=>types('単勝','馬連','ワイド').filter(axisPick),3,'上位の能力差'],
 [gap<1.25,()=>types('ワイド','馬連','3連複'),6,'上位能力が接近'],
 [validated('chaos','high'),()=>types('ワイド','3連複'),6,'検証済み波乱条件'],
 [validated('pace','slow'),()=>special('style','逃げ'),4,'確認できた逃げ有利'],
 [validated('styleBias','先行'),()=>special('style','先行'),4,'確認できた先行有利'],
 [validated('styleBias','差し'),()=>special('style','差し'),4,'確認できた差し有利'],
 [validated('pace','fast')&&validated('styleBias','追込'),()=>special('style','追込'),4,'確認できたハイペース・追込有利'],
 [validated('laneBias','inner'),()=>pool.filter(t=>t.horses.some(id=>active.find(h=>h.id===id)?.frame<=3)),4,'検証済み内枠傾向'],
 [validated('laneBias','outer'),()=>pool.filter(t=>t.horses.some(id=>active.find(h=>h.id===id)?.frame>=6)),4,'検証済み外枠傾向'],
 [validated('goingAdvantage',true),()=>pool.filter(t=>t.horses.some(id=>active.find(h=>h.id===id)?.goingAdvantage===true)),4,'検証済み重馬場適性'],
 [s.model.workoutAdopted===true&&validated('workoutImproved',true),()=>pool.filter(t=>t.horses.some(id=>evidence.workoutImproved.horseIds?.includes(id))),4,'正式採用された追い切り特徴量の改善'],
 [true,()=>pool,4,'保守的な期待回収率の優位性']
 ];
 const comparisons=specs.map(([condition,select,limit,reason],i)=>{
  const picks=condition?select().slice(0,Math.min(limit,config.maxPoints)):[];
  if(!picks.length)return {...definitions[i],state:'unavailable',reason:condition?'発売・確率・時刻付きオッズ・期待値の条件不足':'発動条件不足',picks:[]};
  const metrics=portfolio(picks,d.outcomes,s.race.placeSlots);
  // Provisional utility, not tuned on these results. Lowest-odds scenario.
  const utility=(metrics.expectedReturn-1)*Math.sqrt(metrics.hitProbability)-.01*picks.length-.05*metrics.lossOnHitProbability;
  return {...definitions[i],state:'candidate',reason,picks,metrics,utility};
 });
 const ranked=comparisons.filter(x=>x.state==='candidate'&&x.utility>0).sort((a,b)=>b.utility-a.utility||a.id-b.id),best=ranked[0];
 comparisons.push({...definitions[29],state:best?'not-selected':'selected',reason:best?'比較条件を満たす候補あり':'期待値・情報・リスクの条件を満たす候補なし'});
 // One final set; duplicate strategies are internal alternatives, never extra UI sets.
 return {...base,state:best?'proposed':'skip',strategies:[best?.id||30],picks:best?best.picks.map(({horses,...t})=>t):[],reason:best?.reason||'期待値・情報・リスクの条件を満たす候補なし',metrics:best?.metrics||null,comparisons};
}
function portfolio(picks,outcomes,placeSlots){
 let hitProbability=0,lossOnHitProbability=0,expectedReturn=0;const cost=picks.reduce((n,t)=>n+(t.amount??100),0);
 if(cost===0)return {hitProbability:0,expectedReturn:null,lossOnHitProbability:0,points:0,riskBasis:'購入金額0円・購入なし'};
 for(const o of outcomes){const pay=picks.reduce((n,t)=>n+(isHit(t,o,placeSlots)?t.odds[0]*(t.amount??100):0),0);expectedReturn+=o.p*pay/cost;if(pay>0){hitProbability+=o.p;if(pay<cost)lossOnHitProbability+=o.p}}
 return {hitProbability,expectedReturn,lossOnHitProbability,points:picks.filter(t=>(t.amount??100)>0).length,riskBasis:(picks.some(t=>'amount' in t)?'入力金額による比較':'各点100円の均等比較・購入額未設定')+'・現在オッズ下限の暫定リスク。通常着順の近似で同着を予測しない'};
}
function amounts(picks,values){
 if(!values||Object.keys(values).length===0)return {total:null,picks:clone(picks)};
 const known=new Set(picks.map(t=>t.type+':'+t.pick));if(Object.keys(values).some(k=>!known.has(k)))throw Error('候補外の金額');
 const result=picks.map(t=>{const amount=values[t.type+':'+t.pick]??0;if(!Number.isInteger(amount)||amount<0||amount%100)throw Error('購入額は100円単位');return {...t,amount}}),total=result.reduce((n,t)=>n+t.amount,0);
 if(total>5000)throw Error('1レース上限5,000円');return {total,picks:result};
}
function update(previous,snapshot,now,reason,options={}){
 if(!beforeStart(snapshot,now))return {record:previous||null,changed:false,locked:true};
 // Ignore memo, raw text, display fields and acquisition-only timestamp updates.
 const odds=Object.fromEntries(Object.entries(snapshot.odds||{}).map(([type,m])=>[type,{values:m.values,fresh:validTime(m.asOf)&&Date.parse(m.asOf)<Date.parse(snapshot.race.startAt)&&Date.parse(m.asOf)<=Date.parse(now)&&Date.parse(now)-Date.parse(m.asOf)<=(options.oddsMaxAgeMs||1800000)}]));
 const relevant={integrity:eligibleSnapshot(snapshot,now),race:snapshot.race,forecast:{status:snapshot.forecast.status,kind:snapshot.forecast.kind},model:snapshot.model,runners:snapshot.runners,odds,context:snapshot.context,workouts:snapshot.workouts};
 const sig=signature(relevant);
 if(previous?.inputSignature===sig)return {record:previous,changed:false,locked:false};
 const next=evaluate(snapshot,now,options),old=previous?.current;
 const history=[...(previous?.history||[]),{at:now,reason,sourceTimes:clone(snapshot.sources||{}),before:old?{marks:old.marks,picks:old.picks,probabilities:old.probabilities,strategies:old.strategies}:null,after:{marks:next.marks,picks:next.picks,probabilities:next.probabilities,strategies:next.strategies},missing:next.missing,modelVersion:next.modelVersion}];
 return {record:{raceId:snapshot.race.id,inputSignature:sig,current:next,history},changed:true,locked:false};
}
function settle(record,result){
 if(!record?.current||record.current.kind!=='pre-race'||!validTime(result.startAt)||Date.parse(record.current.generatedAt)>=Date.parse(result.startAt)||result.status!=='results')return null;
 const picks=record.current.picks;if(!picks.length)return {status:'skip',cost:0,returned:0};
 if(picks.some(t=>!result.payouts.some(p=>p.type===t.type&&Number.isFinite(p.amount))))return {status:'pending',cost:null,returned:null};
 // Evaluation rule fixed before observing outcomes: 100 yen per unique ticket.
 const unique=[...new Map(picks.map(t=>[t.type+':'+t.pick,t])).values()],cost=unique.length*100;
 const paid=new Map();for(const p of result.payouts){const k=p.type+':'+key(p.type,p.pick.split('-').map(Number));if(!paid.has(k))paid.set(k,p.amount)}
 const returned=unique.reduce((n,t)=>n+(paid.get(t.type+':'+t.pick)||0),0);
 return {status:returned?'hit':'miss',cost,returned,net:returned-cost,torigami:returned>0&&returned<cost,points:unique.length};
}
function applyAdoptedModel(snapshot,registration){
 const base=clone(snapshot),a=registration?.audit;
 if(!a?.approved||!a.chronological||!a.leakageChecked||!a.heldOutImproved||!a.artifactSignature||!a.baselineSignature||a.baselineSignature!==base.model.signature||typeof registration.predict!=='function'||!a.validationThrough||a.validationThrough>=base.race.date||!a.features?.length)return base;
 const workoutUsed=a.features.includes('workouts');
 const confirmed=Object.values(base.workouts||{}).some(w=>w.available&&w.sessions?.length);
 if(workoutUsed&&!confirmed)return base;
 const predicted=registration.predict(clone(base));
 const active=base.runners.filter(h=>!h.withdrawn);
 if(!predicted||active.some(h=>!(predicted[h.id]>0)||!Number.isFinite(predicted[h.id])))throw Error('正式モデルの出力が不完全です');
 for(const h of active)h.weight=predicted[h.id];
 base.model={...base.model,version:registration.version,signature:a.artifactSignature,calibrated:true,workoutAdopted:workoutUsed};
 if(workoutUsed){const improved=active.filter(h=>base.workouts[h.id]?.available&&predicted[h.id]>snapshot.runners.find(x=>x.id===h.id).weight).map(h=>h.id);base.context={...base.context,workoutImproved:{validated:true,value:improved.length>0,horseIds:improved}}}
 return base;
}
const api={VERSION,TYPES,definitions,key,signature,beforeStart,distributions,evaluate,update,amounts,settle,isHit,portfolio,applyAdoptedModel};root.StrategyV7=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
