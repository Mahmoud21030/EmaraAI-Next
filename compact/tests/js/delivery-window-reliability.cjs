const fs = require('node:fs');const vm = require('node:vm');const assert = require('node:assert/strict');
const text = fs.readFileSync('extension/sw.js','utf8');
const source = text.slice(text.indexOf('let deliveryWin = null;'), text.indexOf('// A NEW chat', text.indexOf('let deliveryWin = null;')));
function make(saved, failMove=false) {
 const tabs = new Map([[1,{id:1,windowId:10,groupId:5,active:false,url:"https://chatgpt.com/c/old"}],[2,{id:2,windowId:10,groupId:99,active:true}],[3,{id:3,windowId:20,groupId:6,active:false}]]);
 let creates=0;
 const ctx={WIN:{},withoutStealingFocus:fn=>fn(),groupTab:async()=>{},chrome:{
  storage:{session:{get:async()=>({}),set:async()=>{}},local:{get:async()=>({deliveryWin:saved}),set:async()=>{}}},
  tabGroups:{query:async()=>[{id:5,windowId:10,title:"EmaraAI"},{id:6,windowId:20,title:"EmaraAI delivery"}]},
  windows:{get:async id=>{if (![10,20].includes(id)) throw Error('closed');return {id,type:'normal',state:'normal'};},create:async()=>{creates++;return {id:30};}},
  tabs:{query:async a=>[...tabs.values()].filter(t=>t.windowId===a.windowId),get:async id=>tabs.get(id),
   move:async(id,a)=>{if(failMove)throw Error('Chrome refused moving tab');tabs.get(id).windowId=a.windowId;},
   update:async(id,a)=>Object.assign(tabs.get(id),a)}
 }};
 vm.createContext(ctx);vm.runInContext(source,ctx);return {ctx,tabs,creates:()=>creates};
}
(async()=>{
 const mixed=make();assert.equal((await mixed.ctx.deliveryWindow()).id,10);assert.equal(mixed.creates(),0);
 const persisted=make(20);assert.equal((await persisted.ctx.deliveryWindow()).id,20);
 const stale=make(999);assert.equal((await stale.ctx.deliveryWindow()).id,10);
 const moved=make(20);await moved.ctx.intoDeliveryWindow(moved.tabs.get(1),false);assert.equal(moved.tabs.get(1).windowId,20);assert.equal(moved.tabs.get(1).active,false);
 const failed=make(20,true);await assert.rejects(failed.ctx.intoDeliveryWindow(failed.tabs.get(1)),/refused moving/);assert.equal(failed.tabs.get(1).windowId,10);
 console.log('5 shared delivery window checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
