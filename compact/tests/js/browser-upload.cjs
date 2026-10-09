const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('extension/sw.js', 'utf8');
let input = {tagName:'INPUT',type:'file',disabled:false,multiple:false,events:[],dispatchEvent(e){this.events.push(e.type);}};
class Transfer { constructor(){this.files=[];this.items={add:f=>this.files.push(f)};} }
class File {constructor(parts,name,opts){this.name=name;this.size=parts[0].length;this.type=opts.type;}}
const ctx={document:{querySelector:()=>input},DataTransfer:Transfer,File,Uint8Array,atob:s=>Buffer.from(s,'base64').toString('binary'),Event:class{constructor(type){this.type=type;}}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function pageUpload(a)'),source.indexOf('const captureInTurn')),ctx);
const args={selector:'#file',files:[{name:'a.json',type:'application/json',data:'e30='}]};
const result=ctx.pageUpload(args);
assert.equal(result.files[0].bytes,2);assert.equal(input.files[0].name,'a.json');assert.deepEqual(input.events,['input','change']);
assert.throws(()=>ctx.pageUpload({...args,files:[...args.files,...args.files]}),/one file/);
input.disabled=true;assert.throws(()=>ctx.pageUpload(args),/enabled file input/);
input=null;assert.throws(()=>ctx.pageUpload(args),/enabled file input/);
console.log('6 browser upload checks passed');
