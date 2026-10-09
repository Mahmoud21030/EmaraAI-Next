const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('extension/sw.js', 'utf8');
let tab = {id:1, url:'https://chatgpt.com.evil.example/c/12345678'};
let created = 0;
const ctx = {URL, chrome:{tabs:{get:async()=>tab, query:async()=>[], create:async()=>{created++;return tab;}}}, sleep:async()=>{},waitLoaded:async()=>{}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('const providerURL ='),source.indexOf('async function chatgptTab')),ctx);
vm.runInContext(source.slice(source.indexOf('async function webTab'),source.indexOf('// ---------------------------------------------------------------- delivery pool:')),ctx);
(async()=>{
  for(const bad of ['https://chatgpt.com.evil.example/c/12345678','https://evil.example/chatgpt.com/c/12345678','http://chatgpt.com/c/12345678','https://chatgpt.com@evil.example/c/12345678','https://chatgpt.com/?next=/c/12345678']) {
    ctx.bad=bad;assert.equal(vm.runInContext('chatIdOf(bad)',ctx),'');
  }
  assert.equal(vm.runInContext("chatIdOf('https://chatgpt.com/g/g-p-example/c/12345678')",ctx),'12345678');
  assert.equal(await ctx.findTab({tab_id:'1',url:'https://chatgpt.com/c/12345678'}),null);
  tab={id:1,url:'https://claude.ai.evil.example/chat/12345678'};
  await assert.rejects(ctx.webTab({tab_id:'1',url:tab.url,host:'claude.ai',chat_pattern:'claude\\.ai/chat/[a-z0-9]+'}),/not found/);
  assert.equal(created,0);
  tab={id:1,url:'https://claude.ai/chat/12345678'};
  assert.equal((await ctx.webTab({tab_id:'1',host:'claude.ai',chat_pattern:'claude\\.ai/chat/[a-z0-9]+'})).id,1);
  console.log('9 provider identity checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
