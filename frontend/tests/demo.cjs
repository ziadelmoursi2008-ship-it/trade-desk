const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');

require.extensions['.ts'] = (module,filename) => {
  const source = fs.readFileSync(filename,'utf8');
  const result = ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2023},fileName:filename});
  module._compile(result.outputText,filename);
};
const {demoRequest} = require('../src/demo.ts');

(async()=>{
  const all = await demoRequest('/api/scan?min_strength=65&mode=gemini');
  assert.equal(all.matches.length,all.analyzed,'Scanner must return every eligible market');
  assert.equal(all.eligible,all.matches.length);
  assert.equal(all.best.ticker,all.matches[0].ticker);
  for (const item of all.matches) {
    const signed = item.technical.indicator_groups.trend_structure.score;
    assert.equal(item.decision.score,Math.round((signed+100)/2),'Signed technical direction must map to the 0–100 scale');
    assert(item.decision.score>=0&&item.decision.score<=100);
    assert(item.decision.strength>=65&&item.decision.strength<=100);
  }
  const nvidia = all.matches.find(item=>item.ticker==='NVDA').decision;
  assert.equal(nvidia.score,0);
  assert.equal(nvidia.strength,92,'Direction normalization must preserve signal strength');
  const cutoff=all.matches.at(-1).decision.strength;
  const filtered=await demoRequest('/api/scan?min_strength='+cutoff+'&mode=gemini');
  assert.deepEqual(filtered.matches.map(item=>item.ticker),all.matches.filter(item=>item.decision.strength>=cutoff).map(item=>item.ticker));
  for (const direction of ['BUY','SELL']) {
    const result=await demoRequest('/api/scan?min_strength=65&direction='+direction);
    assert.deepEqual(result.matches.map(item=>item.ticker),all.matches.filter(item=>item.decision.signal_type===direction).map(item=>item.ticker));
  }
  const selected=await demoRequest('/api/scan?min_strength=65&symbols=NVDA,AAPL');
  assert.equal(selected.matches.length,2);
  const empty=await demoRequest('/api/scan?min_strength=95');
  assert.deepEqual(empty.matches,[]);
  assert.equal(empty.eligible,0);
  assert.equal(empty.best,null);
  console.log('PASS demo score scale, independent signal strength, all scanner matches, inclusive cutoff, direction, watchlist and empty results');
})().catch(error=>{console.error(error);process.exitCode=1;});
