/* Loaded only by scripts/prepare-ver7-site.py in an unpublished development checkout. */
(function(){
'use strict';
if(window.VER7_DEVELOPMENT!==true)return;
const core=window.StrategyV7,baselineAI=aiForRace,pendingForecasts=new Map();
const originalSave=save,originalRender=render,originalFeed=applyLiveFeed;
let busy=false;
function startAt(r){return /^\d{2}:\d{2}$/.test(r.startTime||'')?r.date+'T'+r.startTime+':00+09:00':null}
function forecast(r){return pendingForecasts.get(r.id)||baselineAI(r)}
function workoutEvidence(r){
 const features=typeof workoutTextFeaturesForRace==='function'?workoutTextFeaturesForRace(r):{};
 // Parser's confirmedFeatures already rejects uncertain clocks and identities.
 return Object.fromEntries(Object.entries(features).map(([id,f])=>[id,{available:f.available,adopted:false,score:null,
  sessions:(f.sessions||[]).map(s=>({date:s.date,course:s.course,going:s.going,times:s.times,rider:s.rider,effort:s.effort,partners:s.partners,position:s.position})),
  previous:(f.previous||[]).map(s=>({date:s.date,course:s.course,going:s.going,times:s.times,effort:s.effort})),status:f.status}]));
}
function snapshot(r){
 const p=forecast(r)||{},hs=horsesForRace(r.id),c=VALUE_CALIBRATION;
 const calibrated=Number.isFinite(c.temperature)&&c.temperature>0&&c.audit?.raceLogLoss<c.auditUniform?.raceLogLoss&&c.audit?.multiclassBrier<c.auditUniform?.multiclassBrier&&p.group==='平地'&&typeof p.modelSignature==='string'&&p.modelSignature===c.modelSignature;
 const strength=h=>{const v=p.items?.[h.id]?.winScore;if(!Number.isFinite(v)||v<=0||v>=1)return null;return Math.exp(Math.max(-50,Math.min(50,Math.log(v/(1-v))/(c.temperature||1))))};
 const stamp=valueOddsAsOf.get(r.id)||null,odds={};
 for(const [type,values] of Object.entries(ODDS_DATA[r.id]||{}))odds[type]={asOf:stamp,values};
 const excluded=hs.filter(h=>['取消','除外'].includes(h.finish)||h.entryState==='cancelled').length;
 const evidence=workoutEvidence(r);
 return {race:{id:r.id,date:r.date,startAt:startAt(r),status:r.status,availableTypes:core.TYPES.filter(t=>t!=='枠単'),placeSlots:Number(r.registeredField||r.field)>=8?3:2,
  going:r.going||r.weather||null,field:r.field},
 forecast:{status:p.status,kind:p.forecastKind,generatedAt:p.forecastGeneratedAt||null},
 model:{version:AI_PREDICTIONS.version+'-calibration-'+c.version,signature:p.modelSignature||null,calibrated:calibrated&&!excluded,validatedTypes:['単勝'],workoutAdopted:false},
 runners:hs.filter(h=>h.entryState!=='not_listed').map(h=>({id:h.id,number:Number(h.number),frame:Number(h.frame),weight:strength(h),withdrawn:['取消','除外'].includes(h.finish)||h.entryState==='cancelled',
  jockey:h.jockey,load:h.load,style:p.items?.[h.id]?.evidence?.style||null,historyDates:(h.history||[]).filter(t=>t.date<r.date).map(t=>t.date)})),
 odds,workouts:evidence,context:{},sources:{entries:r.fetchedAt||null,forecast:p.forecastGeneratedAt||null,odds:stamp}};
}
function refresh(reason){
 if(busy)return false;busy=true;let changed=false;
 try{
  db.ver7??={records:{},amounts:{}};
  for(const r of races().filter(r=>r.id.startsWith('jra-')&&r.status==='entries')){
   const now=new Date().toISOString();if(!startAt(r)||Date.parse(startAt(r))<=Date.parse(now))continue;
   const s=core.applyAdoptedModel(snapshot(r),window.Ver7ApprovedModel||null),old=db.ver7.records[r.id],result=core.update(old,s,now,reason);
   if(result.changed){db.ver7.records[r.id]=result.record;if(core.signature(old?.current.picks)!==core.signature(result.record.current.picks))delete db.ver7.amounts[r.id];changed=true}
  }
 }finally{busy=false}return changed;
}
// save() is the common successful persistence boundary for text, image and PDF.
// No draft is read here; only confirmed parser features enter the snapshot.
save=function(){const prior=JSON.stringify(db.ver7||null);try{refresh('確認済みデータの保存・修正');if(originalSave())return true}catch(e){toast('Ver.7更新を保存できません：'+e.message)}db.ver7=JSON.parse(prior);return false};
render=function(){const prior=JSON.stringify(db.ver7||null);try{if(refresh('表示時の最新条件確認')&&!originalSave())db.ver7=JSON.parse(prior)}catch(e){db.ver7=JSON.parse(prior);console.error('Ver.7',e)}return originalRender()};
applyLiveFeed=function(feed){
 const result=originalFeed(feed);
 for(const [id,f] of Object.entries(feed.jraForecasts||{})){const r=race(id),start=r&&startAt(r),old=pendingForecasts.get(id);if(r&&r.status==='entries'&&start&&Date.now()<Date.parse(start)&&f.forecastKind==='pre-race'&&Date.parse(f.forecastGeneratedAt)<Date.parse(start)&&(!old||Date.parse(f.forecastGeneratedAt)>Date.parse(old.forecastGeneratedAt)))pendingForecasts.set(id,f)}
 const prior=JSON.stringify(db.ver7||null);if(refresh('出馬表・馬場・オッズ・取消・騎手・予測モデルの更新')&&!originalSave())db.ver7=JSON.parse(prior);return result;
};
function display(r){
 const rec=db.ver7?.records?.[r.id],p=rec?.current,locked=!startAt(r)||Date.now()>=Date.parse(startAt(r))||r.status!=='entries';
 if(!p)return '<div class="notice">Ver.7開発版：発走前の予想セットが未保存です。結果判明後の予想は作成しません。</div>';
 const names=p.strategies.map(n=>core.definitions[n-1].name),grouped=new Map();for(const t of p.picks){if(!grouped.has(t.type))grouped.set(t.type,[]);grouped.get(t.type).push(t)}
 const selection=core.amounts(p.picks,db.ver7.amounts[r.id]||{});
 const di=p.distributionInputs,metrics=di?core.portfolio(selection.picks,core.distributions(di.runners,di.placeSlots).outcomes,di.placeSlots):null;
 const hmap=new Map(horsesForRace(r.id).map(h=>[h.id,h]));
 return `<div class="notice"><strong>Ver.7 開発検証版・本番採用前</strong><p>${locked?'発走後の固定済み事前予想':'発走前の開発予想'} · ${esc(p.generatedAt)}</p><p>${Object.entries(p.marks).filter(([,m])=>m).map(([id,m])=>esc(m+' '+(hmap.get(id)?.number||'')+' '+(hmap.get(id)?.name||id))).join(' ／ ')}</p><p>◎の信頼度：${esc(p.confidence)}。馬場・展開の検証済み指標がない場合、数値を推測しません。</p><h4>戦略：${esc(names.join('・'))}${p.state==='skip'?'（見送り）':''}</h4><p>${esc(p.reason)}</p>${[...grouped].map(([type,rows])=>`<h4>${esc(type)} · ${rows.length}点</h4>${rows.map(t=>`<div class="row"><strong>${esc(t.pick)}</strong><span>推定的中率 ${(100*t.probability).toFixed(1)}% · オッズ ${t.odds.map(n=>n.toFixed(1)).join('〜')}倍 · 期待回収率 ${(100*t.expectedReturn).toFixed(1)}%</span>${!locked?`<label>金額（任意）<input type="number" min="0" step="100" max="5000" value="${db.ver7.amounts[r.id]?.[t.type+':'+t.pick]??''}" onchange="setVer7Amount('${esc(r.id)}','${esc(t.type+':'+t.pick)}',this.value)"></label>`:''}</div>`).join('')}`).join('')}<p>合計 ${p.picks.length}点 ／ 購入金額：${selection.total===null?'未設定':money(selection.total)}（上限5,000円）</p>${metrics?`<p>暫定トリガミリスク：${(100*metrics.lossOnHitProbability).toFixed(1)}% · ${esc(metrics.riskBasis)}</p>`:''}<p>追い切り：確定データのみ参考表示。未採用のため勝率への加点・欠損減点なし。組合せ確率は通常着順の近似です。同着の払戻は確定結果で照合します。購入・利益を保証せず、購入記録に自動登録しません。</p><details><summary>予想の変更履歴（${rec.history.length}件）</summary>${rec.history.slice().reverse().map(x=>`<div><strong>${esc(x.at)} · ${esc(x.reason)}</strong><p>買い目：${esc((x.before?.picks||[]).map(t=>t.type+' '+t.pick).join('、')||'なし')} → ${esc(x.after.picks.map(t=>t.type+' '+t.pick).join('、')||'見送り')}</p><details><summary>印・確率・取得時刻・欠損・モデルの詳細</summary><pre>${esc(JSON.stringify(x,null,2))}</pre></details></div>`).join('')}</details></div>`;
}
aiTicketSection=function(r){return `<div class="ai-ticket-section"><h3>AI最終予想</h3>${display(r)}</div>`};
aiTicketViewContent=display;
// Development replaces the summary as a whole: no second mark grid or legacy
// candidate list remains beside the final set. Legacy source/data are retained.
aiRaceSummary=function(r){return `<section class="card ai-summary"><h2>AI最終予想</h2>${display(r)}</section>`};
aiTicketPlan=function(r){const p=db.ver7?.records?.[r.id]?.current;return {picks:p?.picks||[],budget:null,formation:null,reasons:p?[p.reason]:[],strategy:p?.strategies.map(n=>core.definitions[n-1].name).join('・')||'見送り'}};
valueDisplay=function(){return ''};

window.setVer7Amount=function(id,k,value){try{const r=race(id);if(!r||r.status!=='entries'||Date.now()>=Date.parse(startAt(r)))throw Error('発走後の予想金額は変更できません');const prior=db.ver7.amounts[id]||{},next={...prior};if(value==='')delete next[k];else next[k]=Number(value);core.amounts(db.ver7.records[id].current.picks,next);db.ver7.amounts[id]=next;if(!originalSave())db.ver7.amounts[id]=prior;render()}catch(e){toast(e.message);render()}};
window.Ver7Development={snapshot,refresh,display};
// Existing saved forecast marks remain untouched; the unified section uses
// its own versioned marks. Closed races without a snapshot remain unrecorded.
setInterval(()=>{if(typeof selected==='string'&&selected)render()},60000);
render();
})();
