/* Strict replay audit: no final odds substituted, no post-start generation. */
const fs=require('node:fs'),C=require('../site-overlay/dist/strategy-v7.js');
const load=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^const \w+\s*=\s*/,'').trim().replace(/;$/,''));
const d=load('dist/results-data.js'),forecasts=load('dist/jra-forecasts.json'),odds=load('dist/jra-odds.json');
const calibrationPath=process.argv[2];const cal=calibrationPath?load(calibrationPath):{};
const rows=[],why={},summaries=[];
for(const r of d.races){
 const f=forecasts[r.id],startAt=r.date+'T'+r.startTime+':00+09:00';
 if(r.status!=='results'||!f||!odds.preRace?.[r.id])continue;
 const start=Date.parse(startAt),generated=Date.parse(f.forecastGeneratedAt),saved=odds.preRace[r.id];
 if(f.forecastKind!=='pre-race'||!Number.isFinite(generated)||generated>=start){why['発走前予想なし']=(why['発走前予想なし']||0)+1;continue}
 const eligibleOdds=Object.entries(saved).filter(([,m])=>Number.isFinite(Date.parse(m.observedAt))&&Date.parse(m.observedAt)<start);
 if(!eligibleOdds.length){why['発走前オッズなし']=(why['発走前オッズなし']||0)+1;continue}
 const nowMs=Math.max(generated,...eligibleOdds.map(([,m])=>Date.parse(m.observedAt))),now=new Date(nowMs).toISOString();
 // All odds must already be available at this decision time; stale types rejected by core.
 const horses=d.horses.filter(h=>h.raceId===r.id&&h.entryState!=='not_listed');
 if(horses.length!==Object.keys(f.items).length||horses.some(h=>!f.items[h.id])){why['出走馬ID不一致']=(why['出走馬ID不一致']||0)+1;continue}
 const leaky=Object.values(f.items).some(x=>(x.evidence?.recent||[]).some(z=>z.date>=r.date||(z.rivals||[]).some(v=>v.next?.date>=r.date)));
 if(leaky){why['未来情報混入']=(why['未来情報混入']||0)+1;continue}
 const proven=typeof f.modelSignature==='string'&&f.modelSignature===cal.modelSignature;
 const snapshot={race:{id:r.id,date:r.date,status:'entries',startAt,placeSlots:Number(r.registeredField||r.field)>=8?3:2,availableTypes:C.TYPES.filter(t=>t!=='枠単')},forecast:{kind:f.forecastKind,status:f.status,generatedAt:f.forecastGeneratedAt},model:{version:f.modelVersion||'unversioned-existing',calibrated:proven,validatedTypes:proven?['単勝']:[],workoutAdopted:false},
 runners:horses.map(h=>{const x=f.items[h.id],p=x.winScore;return {id:h.id,number:Number(x.number),frame:null,weight:Number.isFinite(p)&&p>0&&p<1?Math.exp(Math.log(p/(1-p))/(cal.temperature||1)):null,historyDates:(x.evidence?.recent||[]).map(z=>z.date)}}),
 odds:Object.fromEntries(eligibleOdds.map(([type,m])=>[type,{asOf:m.observedAt,values:m.odds}])),sources:{forecast:f.forecastGeneratedAt},context:{},workouts:{}};
 const rec=C.update(null,snapshot,now,'保存済み事前入力の検証').record;
 const settlement=C.settle(rec,{status:r.status,startAt,payouts:r.payouts||[]});
 const rawTop=f.ranked?.[0],actual=horses.find(h=>h.id===rawTop),hasTop=f.status==='predicted'&&actual;
 rows.push({id:r.id,date:r.date,forecastAt:f.forecastGeneratedAt,decisionAt:now,kind:f.forecastKind,modelProvenance:proven,baselineTop:hasTop?{id:rawTop,finish:actual.finish}:null,state:rec?.current.state||'blocked',strategies:rec?.current.strategies||[],picks:rec?.current.picks||[],missing:rec?.current.missing||[],settlement});
}
const tops=rows.filter(r=>r.baselineTop),trades=rows.filter(r=>r.settlement&&['hit','miss'].includes(r.settlement.status));
let equity=0,peak=0,drawdown=0;for(const r of trades.sort((a,b)=>a.decisionAt.localeCompare(b.decisionAt))){equity+=r.settlement.net;peak=Math.max(peak,equity);drawdown=Math.max(drawdown,peak-equity)}
const cost=trades.reduce((n,r)=>n+r.settlement.cost,0),returned=trades.reduce((n,r)=>n+r.settlement.returned,0);
const report={version:C.VERSION,classification:'saved-input-replay-only; Ver.7 itself was not saved before these races',fixedRule:'100 yen per unique ticket; not a purchase record',eligibleSavedInputs:rows.length,excluded:why,baseline:{topTrials:tops.length,topWins:tops.filter(r=>Number(r.baselineTop.finish)===1).length,topPodiums:tops.filter(r=>[1,2,3].includes(Number(r.baselineTop.finish))).length},candidate:{proposed:rows.filter(r=>r.state==='proposed').length,skip:rows.filter(r=>r.state==='skip').length,settledRaces:trades.length,cost,returned,roi:cost?returned/cost:null,profit:cost?returned-cost:null,averagePoints:trades.length?trades.reduce((n,r)=>n+r.settlement.points,0)/trades.length:null,torigamiRate:trades.length?trades.filter(r=>r.settlement.torigami).length/trades.length:null,maxDrawdown:trades.length?drawdown:null},strategyCounts:Object.fromEntries(C.definitions.map(x=>[x.id,rows.filter(r=>r.strategies.includes(x.id)).length])),rows,adoption:{approved:false,reasons:['30戦略の閾値・効用関数は初期設定で未学習','保存予想と確率校正のモデル署名照合が必要','各券種・追い切りの時系列比較と前向き実戦検証が未完了','過去結果を使って閾値を変更していません']}};
fs.mkdirSync('reports',{recursive:true});fs.writeFileSync('reports/ver7-replay.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({...report,rows:undefined},null,2));
