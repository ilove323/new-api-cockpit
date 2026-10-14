import {build} from 'esbuild';
import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.dirname(fileURLToPath(import.meta.url));
const sources=JSON.parse(await readFile(path.join(root,'vendor/sources.json'),'utf8'));
for(const source of sources) {
  const data=await readFile(path.join(root,'vendor',source.file));
  if(createHash('sha256').update(data).digest('hex')!==source.sha256) throw new Error('Upstream source changed: '+source.file);
}
const output=path.resolve(root,'../../src/new_api_cockpit/static/studio-upstream.js');
const result=await build({absWorkingDir:root,entryPoints:[path.join(root,'src/index.ts')],bundle:true,write:false,format:'iife',
  globalName:'CockpitStudioUpstream',target:'es2022',minify:false,legalComments:'inline',
  banner:{js:'/* GPT Image Playground: Copyright (c) 2026 CookSleep, MIT.\n * InvokeAI compositor: Copyright 2022-2026 InvokeAI Contributors, Apache-2.0.\n * Full licenses and notice: GPT-IMAGE-PLAYGROUND-LICENSE, INVOKEAI-LICENSE, INVOKEAI-NOTICE.\n * Built from pinned source. Edit frontend/studio, not this file.\n * Revisions and modifications: docs/image-studio-sources.md. */'},
  footer:{js:"if (typeof module === 'object' && module.exports) module.exports = CockpitStudioUpstream;"}});
const text=result.outputFiles[0].text;
if(process.argv.includes('--check')) {
  if(await readFile(output,'utf8')!==text) throw new Error('Studio source and bundled asset differ; run npm run build');
  console.log('Studio source hashes and built asset match.');
} else {await writeFile(output,text); console.log('Built studio-upstream.js from verified source.');}
