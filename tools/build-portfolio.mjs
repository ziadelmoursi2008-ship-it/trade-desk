import { spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../frontend/', import.meta.url));
const env = {...process.env, VITE_DEMO_MODE:'true', VITE_API_BASE_URL:''};
for (const args of [
  [fileURLToPath(new URL('../frontend/node_modules/typescript/bin/tsc',import.meta.url)), '-b'],
  [fileURLToPath(new URL('../frontend/node_modules/vite/bin/vite.js',import.meta.url)), 'build','--configLoader','native','--outDir','dist-portfolio'],
]) {
  const result = spawnSync(process.execPath,args,{cwd:frontend,env,stdio:'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
const output = new URL('../frontend/dist-portfolio/', import.meta.url);
const source = await readFile(new URL('../backend/observatory/index.html', import.meta.url),'utf8');
await mkdir(new URL('status/',output),{recursive:true});
await writeFile(new URL('status/index.html',output),source.replace('name="app-mode" content="live"','name="app-mode" content="demo"'));
await writeFile(new URL('.nojekyll',output),'');
console.log('Portfolio ready: frontend/dist-portfolio (synthetic data, no backend required).');
