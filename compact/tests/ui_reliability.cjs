const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('src/emaraai_hub/ui/company.js', 'utf8');
function subject() {
  const nodes = Object.fromEntries(['nav', 'view', 'modal', 'pal'].map(id => [id, {innerHTML:'', scrollTop:0}]));
  const document = {activeElement:{tagName:'BODY'}, documentElement:{dataset:{}}, getElementById:id => nodes[id] || null};
  const context = vm.createContext({document, location:{hash:'#/projects/A'}, localStorage:{getItem:()=>null},
    fetch:async()=>({ok:true,json:async()=>({ok:true,key:'new',questions:[],approvals:[]})}), Date, setTimeout, clearTimeout, console});
  vm.runInContext(source.slice(0, source.indexOf('/* ---------- views ---------- */')) + '\nconst VIEWS = {}, AFTER = {}; globalThis.expose = {S,VIEWS,AFTER,render,tick};', context);
  return {context,nodes,document,...context.expose};
}
(async()=>{
  const one = subject(), requests = {};
  one.VIEWS.projects = name => new Promise(resolve => {requests[name] = resolve;});
  const first = one.render(); one.context.location.hash = '#/projects/B'; const second = one.render();
  requests.B('PROJECT B'); await second; requests.A('PROJECT A'); await first;
  assert(one.nodes.view.innerHTML.includes('PROJECT B'));
  const two = subject(); let renders = 0;
  two.VIEWS.projects = async()=>{renders++; return 'fresh';}; two.S.pulse = 'old';
  two.document.activeElement.tagName = 'INPUT'; await two.tick();
  assert.equal(two.S.pulse, 'old');
  two.document.activeElement.tagName = 'BODY'; await two.tick(); assert.equal(renders, 1);
  const three = subject(); three.nodes.drawer = {innerHTML:'',querySelector:()=>null};
  vm.runInContext(source.slice(source.indexOf('async function drawer('),source.indexOf('DRAWERS.person =')) + '\nglobalThis.panel={drawer,DRAWERS};', three.context);
  const panels = {}; three.context.panel.DRAWERS.person = key => new Promise(resolve=>{panels[key]=resolve;});
  const older = three.context.panel.drawer('person','A'), newer = three.context.panel.drawer('person','B');
  panels.B('PERSON B'); await newer; panels.A('PERSON A'); await older;
  assert(three.nodes.drawer.innerHTML.includes('PERSON B'));
  const four = subject(); let wired = 0; four.AFTER.messages = ()=>{wired++;}; four.context.chatWire = ()=>{};
  vm.runInContext(source.match(/AFTER\.projects = .*;/)[0] + '\nglobalThis.projectAfter=AFTER.projects;',four.context);
  four.S.route=['projects','A','messages']; four.context.projectAfter(); assert.equal(wired,1);
  const timers = [];
  const extension = fs.readFileSync('extension/sw.js','utf8');
  const ctx = vm.createContext({setTimeout:cb=>{timers.push(cb);return timers.length;},clearTimeout:()=>{}});
  const start = extension.indexOf('let turn = Promise.resolve();');
  vm.runInContext(extension.slice(start,extension.indexOf('const winMode',start))+'\nglobalThis.inTurn=inTurn;',ctx);
  let finish, nextStarted = false;
  const old = ctx.inTurn(()=>new Promise(resolve=>{finish=resolve;})); await Promise.resolve();
  const rejected = assert.rejects(old,/timed out/); timers[0](); await rejected;
  const next = ctx.inTurn(()=>{nextStarted=true;}); await Promise.resolve();
  assert.equal(nextStarted,false); finish(); await next; assert.equal(nextStarted,true);
  console.log('5 UI and extension reliability regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
