const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const clock={now:Date.parse('2026-11-01T09:00:00+09:00')};
class FakeDate extends Date{constructor(...args){super(...(args.length?args:[clock.now]))}static now(){return clock.now}}
const r={id:'jra-test',date:'2026-11-01',startTime:'09:50',status:'entries',field:6,registeredField:6,fetchedAt:'2026-11-01T08:50:00+09:00'};
const hs=Array.from({length:6},(_,i)=>({id:'h'+i,name:'馬'+i,number:String(i+1),frame:i+1,history:[{date:'2026-10-01'}],jockey:'騎手',load:'55'}));
const forecast={modelSignature:'fixture-hash',status:'predicted',group:'平地',forecastKind:'pre-race',forecastGeneratedAt:'2026-11-01T08:55:00+09:00',items:Object.fromEntries(hs.map((h,i)=>[h.id,{winScore:i===0?.6:.1,mark:i===0?'◎':'△',evidence:{style:'先行'}}]))};
let workouts={},fail=false,writes=0;const ctx={window:{VER7_DEVELOPMENT:true},console,Date:FakeDate,setInterval:()=>{},db:{races:[],horses:[],bets:[],events:[]},save:()=>{writes++;return !fail},render:()=>{},applyLiveFeed:()=>{},races:()=>[r],race:id=>id===r.id?r:null,horsesForRace:()=>hs,aiForRace:()=>forecast,AI_PREDICTIONS:{version:'fixture'},VALUE_CALIBRATION:{modelSignature:'fixture-hash',version:1,temperature:1,audit:{raceLogLoss:1,multiclassBrier:1},auditUniform:{raceLogLoss:2,multiclassBrier:2}},ODDS_DATA:{[r.id]:{'単勝':{'1':[4]}}},valueOddsAsOf:new Map([[r.id,'2026-11-01T08:59:00+09:00']]),workoutTextFeaturesForRace:()=>workouts,esc:s=>String(s).replace(/[<>&"']/g,''),money:n=>'¥'+n,toast:()=>{},aiTicketSection:()=>'',aiTicketViewContent:()=>''};
vm.createContext(ctx);for(const f of ['strategy-v7.js','strategy-v7-app.js'])vm.runInContext(fs.readFileSync('site-overlay/dist/'+f,'utf8'),ctx);
function run(s){return vm.runInContext(s,ctx)}
assert.equal(run('db.ver7.records["jra-test"].history.length'),1);
const initial=run('JSON.stringify(db.ver7.records["jra-test"].current.picks)');
// Confirmation commit changes only reference workout evidence; probabilities stay unchanged.
workouts={h0:{available:true,sessions:[{date:'2026-10-28',course:'栗坂',going:'良',times:{4:54,3:39,2:25,1:12},effort:'馬なり'}],previous:[],status:'confirmed'}};
assert.equal(run('save()'),true);assert.equal(run('db.ver7.records["jra-test"].history.length'),2);assert.equal(run('JSON.stringify(db.ver7.records["jra-test"].current.picks)'),initial);
const html=run('aiTicketSection(race("jra-test"))');assert.equal((html.match(/AI最終予想/g)||[]).length,1);assert.ok(!html.includes('その他のAI'));assert.ok(html.includes('購入金額：未設定'));assert.ok(!html.includes('3,000'));
// Source timestamp refresh is not a forecast revision.
r.fetchedAt='2026-11-01T08:59:00+09:00';run('render()');assert.equal(run('db.ver7.records["jra-test"].history.length'),2);
// Market change re-evaluates ticket value while abilities stay fixed.
ctx.ODDS_DATA[r.id]['単勝']['1']=[1.01];run('applyLiveFeed({jraForecasts:{}})');assert.equal(run('db.ver7.records["jra-test"].current.state'),'skip');assert.equal(run('db.ver7.records["jra-test"].history.length'),3);
// Failed persistence rolls back forecast history, preserving prior saved record.
fail=true;r.going='重';run('save()');assert.equal(run('db.ver7.records["jra-test"].history.length'),3);fail=false;
// Fresh pre-start forecast replaces development prediction input only.
const changed=JSON.parse(JSON.stringify(forecast));changed.forecastGeneratedAt='2026-11-01T08:59:00+09:00';changed.items.h1.winScore=.8;ctx.changed=changed;run('applyLiveFeed({jraForecasts:{"jra-test":changed}})');assert.equal(run('db.ver7.records["jra-test"].current.marks.h1'),'◎');assert.equal(forecast.items.h1.winScore,.1);
const frozen=run('JSON.stringify(db.ver7.records["jra-test"])');clock.now=Date.parse('2026-11-01T10:00:00+09:00');r.status='results';workouts={};run('save();render();applyLiveFeed({jraForecasts:{"jra-test":changed}})');assert.equal(run('JSON.stringify(db.ver7.records["jra-test"])'),frozen);
assert.deepEqual(ctx.db.bets,[]);assert.deepEqual(ctx.db.horses,[]);
console.log('PASS integration: initial snapshot, confirmed workout, neutral model, unified display, no preset stake, source refresh dedup, odds refresh, persistence rollback, fresh forecast handoff, post-start lock, legacy data preservation');
