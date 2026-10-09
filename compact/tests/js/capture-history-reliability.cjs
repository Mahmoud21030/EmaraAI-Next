const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const text = fs.readFileSync('extension/sw.js', 'utf8');
const source = text.slice(text.indexOf('let browserCaptureTail ='), text.indexOf('// ---------------------------------------------------------------- connection:'));
let active = 9, captured = 0, historyMoves = [], forceRace = false;
const ctx = { OPS: {}, inTurn: fn => fn(), sleep: async () => {},
  tabFor: async () => ({id: 1, windowId: 2, url: 'http://localhost:5198/#/today'}),
  waitLoaded: async () => ({id: 1, url: 'http://localhost:5198/#/projects'}),
  run: async (id, fn, args) => fn(...args),
  history: {length: 3, go: delta => historyMoves.push(delta)},
  chrome: {tabs: {
    query: async () => [{id: active}],
    update: async (id) => {active = forceRace ? 8 : id;},
    captureVisibleTab: async () => {captured++; return 'data:image/png;base64,abc';},
    goBack: async () => {throw new Error('Cannot find a next page in history.');},
    goForward: async () => {throw new Error('Cannot find a next page in history.');},
    reload: async () => {},
  }},
};
vm.createContext(ctx);vm.runInContext(source, ctx);
(async () => {
  const shot = await ctx.OPS.browser({action: 'screenshot', tab_id: '1'});
  assert.equal(shot.tab_id, '1');assert.equal(captured, 1);assert.equal(active, 9);
  forceRace = true;
  await assert.rejects(ctx.OPS.browser({action: 'screenshot', tab_id: '1'}), /another tab became active/);
  assert.equal(captured, 1);
  forceRace = false;
  const back = await ctx.OPS.browser({action: 'back', tab_id: '1'});
  assert.deepEqual(historyMoves, [-1]);assert.equal(back.url_changed, true);
  await ctx.OPS.browser({action: 'forward', tab_id: '1'});
  assert.deepEqual(historyMoves, [-1, 1]);
  ctx.history.length = 1;
  await assert.rejects(ctx.OPS.browser({action: 'back', tab_id: '1'}), /Cannot find/);
  console.log('5 browser capture/history checks passed');
})().catch(error => { console.error(error);process.exitCode=1; });
