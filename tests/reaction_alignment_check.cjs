const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync(require('node:path').join(__dirname,'../web/app.js'),'utf8');
const ctx={}; vm.createContext(ctx);
for (const name of ['reactionAt','reactionSamples']) {
  const start=src.indexOf(`  function ${name}(`);
  if(start>=0) vm.runInContext(src.slice(start,src.indexOf('\n  }',start)+4),ctx);
}
test('missing minutes are not interpolated or extrapolated',()=>{
  assert.equal(ctx.reactionAt([[0,0],[30,.03]],60),null);
  assert.equal(ctx.reactionAt([[0,0],[60,.06]],30),null);
  assert.equal(ctx.reactionAt([[0,0],[30,.03]],30),.03);
});
test('a linked disclosure and two articles count once, unrelated events stay separate',()=>{
  const rows=[{id:'a',event_key:'d1',v:.05},{id:'b',event_key:'d1',v:.05},
    {id:'d1',event_key:'d1',v:.05},{id:'other',event_key:'d2',v:-.01}];
  const values=ctx.reactionSamples(rows,x=>x.v);
  assert.equal(values.length,2);
  assert.ok(Math.abs(values.reduce((a,b)=>a+b,0)/values.length-.02)<1e-10);
  assert.equal(ctx.reactionSamples(rows.filter(x=>x.id==='a'),x=>x.v).length,1);
});
test('duplicate records with conflicting measurements are excluded from aggregate',()=>{
  assert.equal(ctx.reactionSamples([{event_key:'d1',v:.05},{event_key:'d1',v:.09}],x=>x.v).length,0);
});
