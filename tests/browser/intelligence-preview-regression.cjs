/* Offline preview regression: HTTP/HTTPS, two tabs and sequential frame names.
 * NODE_PATH=<playwright modules> COCKPIT_BROWSER_EXECUTABLE=<chromium> node tests/browser/intelligence-preview-regression.cjs
 * All pages, API responses and preview documents are local synthetic fixtures.
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const staticRoot=path.join(root,'src/new_api_cockpit/static');
const rendered=spawnSync(process.env.COCKPIT_PYTHON||path.join(root,'.venv/bin/python'),['-c',
  'import json\nfrom flask import render_template,g\n'
  +'from new_api_cockpit.app import app\nfrom new_api_cockpit import intelligence\n'
  +'results={}\nimages={}\nfor tab in ("tab-a","tab-b"):\n for number in (1,2,3):\n'
  +'  tag=f"{tab}-{number}"\n'
  +'  html=f\'<html><body><h1>{tag}</h1><svg></svg><script>document.body.dataset.ready="yes"</script></body></html>\'\n'
  +'  results[tag]={"html":html,"preview":intelligence.preview_document(html)}\n'
  +'  svg=f\'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400"><title>{tag}</title><script>parent.document.body.dataset.svgExecuted="yes"</script><image href="/must-not-load.png"/><rect width="600" height="400" fill="red"/></svg>\'\n'
  +'  images[tag]={"html":intelligence.extract_html(svg),"svg":svg,"source":"```svg\\n"+svg+"\\n```"}\n'
  +'with app.test_request_context("/cockpit/intelligence/"):\n'
  +' g.current_user={"id":1,"username":"fixture","role":100,"session_id":"fixture-session","expires_at":4102444800}\n'
  +' page=render_template("intelligence.html",site_name="Offline fixture",prompt=intelligence.PROMPT)\n'
  +' print(json.dumps({"page":page,"results":results,"images":images,"csp":intelligence.PREVIEW_CSP}))'],
  {cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},encoding:'utf8'});
assert.equal(rendered.status,0,'Fixture render failed: '+rendered.stderr);
const fixture=JSON.parse(rendered.stdout);
const mime={'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2'};

async function openPage(context,origin,tab,kind='html'){
  const page=await context.newPage(),state={generations:0,previews:0,errors:[],unexpected:[],tag:null};
  page.on('pageerror',error=>state.errors.push(error.message));
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    assert.equal(url.origin,origin,'All browser requests must stay inside the fixture');
    if(url.pathname==='/cockpit/intelligence/'){
      await route.fulfill({contentType:'text/html',headers:{'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"},body:fixture.page});return;
    }
    if(url.pathname.startsWith('/cockpit/static/')){
      const file=path.resolve(staticRoot,decodeURIComponent(url.pathname.slice('/cockpit/static/'.length)));
      assert(file.startsWith(staticRoot+path.sep)&&fs.existsSync(file));
      await route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});return;
    }
    if(url.pathname==='/cockpit/api/intelligence/models'){
      await route.fulfill({json:{rows:[{model:'fixture-model',group:'fixture-group'}]}});return;
    }
    if(url.pathname==='/cockpit/api/intelligence/test'){
      assert.equal(request.method(),'POST');
      assert.deepEqual(request.postDataJSON(),{model:'fixture-model'});
      state.tag=tab+'-'+(++state.generations);
      const value=kind==='svg'?fixture.images[state.tag]:fixture.results[state.tag];
      const {html,svg}=value,source=value.source||html;
      assert.equal(request.headers().accept,'text/event-stream');
      const events=[
        {type:'status',stage:'waiting'},
        {type:'text',text:source.slice(0,20)},
        {type:'text',text:source.slice(20)},
        {type:'result',result:{model:'fixture-model',group:'fixture-group',elapsed_ms:10,html,svg,source,warning:''}},
      ];
      await route.fulfill({contentType:'text/event-stream',body:events.map(event=>'event: '+event.type+'\ndata: '+JSON.stringify(event)+'\n\n').join('')});return;
    }
    if(url.pathname==='/cockpit/api/intelligence/preview'){
      assert.equal(request.method(),'POST');assert.equal(request.headers().origin,origin);
      const fields=new URLSearchParams(request.postData());
      assert.equal(fields.get('session_id'),'fixture-session');
      assert.equal(fields.get('html'),fixture.results[state.tag].html);
      state.previews++;
      await route.fulfill({contentType:'text/html',headers:{'Content-Security-Policy':fixture.csp},body:fixture.results[state.tag].preview});return;
    }
    if(url.pathname==='/favicon.ico'){await route.fulfill({status:204});return;}
    state.unexpected.push(url.pathname);await route.abort();
  });
  await page.goto(origin+'/cockpit/intelligence/');
  await page.waitForFunction(()=>!document.getElementById('intelligence-start').disabled);
  const secure=await page.evaluate(()=>({secure:isSecureContext,uuid:typeof crypto.randomUUID}));
  assert.equal(secure.secure,origin.startsWith('https:'));
  assert.equal(secure.uuid,secure.secure?'function':'undefined');
  return {page,state};
}

async function expectPreview(target,name,tag){
  const {page}=target,frame=page.locator('#intelligence-preview iframe');
  await frame.waitFor();assert.equal(await frame.getAttribute('name'),name);
  assert.equal(await frame.getAttribute('sandbox'),'allow-scripts');
  assert.equal(await frame.getAttribute('referrerpolicy'),'no-referrer');
  await page.frameLocator('#intelligence-preview iframe').locator('body[data-ready="yes"]').waitFor();
  assert.equal(await page.frameLocator('#intelligence-preview iframe').locator('h1').innerText(),tag);
  assert.equal(await page.locator('#intelligence-status').innerText(),'生成完成。');
}

async function main(){
  const browser=await chromium.launch({headless:true,...(process.env.COCKPIT_BROWSER_EXECUTABLE?{executablePath:process.env.COCKPIT_BROWSER_EXECUTABLE}:{})});
  try{
    for(const origin of ['http://cockpit-preview.test','https://cockpit-preview.test']){
      const context=await browser.newContext();
      try{
        const a=await openPage(context,origin,'tab-a'),b=await openPage(context,origin,'tab-b');
        await a.page.click('#intelligence-start');await expectPreview(a,'cockpit-pelican-1','tab-a-1');
        await b.page.click('#intelligence-start');await expectPreview(b,'cockpit-pelican-1','tab-b-1');
        await Promise.all([a.page.click('#intelligence-replay'),b.page.click('#intelligence-replay')]);
        await expectPreview(a,'cockpit-pelican-2','tab-a-1');await expectPreview(b,'cockpit-pelican-2','tab-b-1');
        assert.equal(a.state.generations,1);assert.equal(b.state.generations,1);
        await a.page.click('#intelligence-source-button');
        assert.equal(await a.page.locator('#intelligence-source').innerText(),fixture.results['tab-a-1'].html);
        await a.page.click('#intelligence-start');await expectPreview(a,'cockpit-pelican-3','tab-a-2');
        await expectPreview(b,'cockpit-pelican-2','tab-b-1');
        await a.page.evaluate(()=>{
          window.dispatchEvent(new PageTransitionEvent('pagehide'));
          window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
        });
        assert.equal(await a.page.locator('#intelligence-preview iframe').count(),0);
        await a.page.click('#intelligence-start');await expectPreview(a,'cockpit-pelican-4','tab-a-3');
        await expectPreview(b,'cockpit-pelican-2','tab-b-1');
        assert.equal(context.pages().length,2,'Preview POST must not open or target another tab');
        assert.deepEqual([a.state.generations,a.state.previews,b.state.generations,b.state.previews],[3,4,1,2]);
        for(const target of [a,b]){assert.deepEqual(target.state.errors,[]);assert.deepEqual(target.state.unexpected,[]);}
        console.log(JSON.stringify({origin,offline:true,tabs:2,tab_a:{generations:3,previews:4},tab_b:{generations:1,previews:2},sandbox:true,no_cross_tab_target:true}));
        await a.page.close();await b.page.close();
        const image=await openPage(context,origin,'tab-a','svg');
        await image.page.click('#intelligence-start');
        await image.page.waitForFunction(()=>{
          const img=document.querySelector('#intelligence-preview > img');
          return img?.complete&&img.naturalWidth>0&&document.getElementById('intelligence-status').textContent==='生成完成。';
        });
        assert.equal(await image.page.locator('#intelligence-preview iframe').count(),0);
        assert.equal(await image.page.locator('#intelligence-preview svg').count(),0,'Never insert SVG markup into the parent DOM');
        assert.equal(await image.page.locator('body').getAttribute('data-svg-executed'),null);
        await image.page.click('#intelligence-replay');
        await image.page.waitForFunction(()=>document.querySelector('#intelligence-preview > img')?.complete);
        await image.page.click('#intelligence-source-button');
        assert.equal(await image.page.locator('#intelligence-source').innerText(),fixture.images['tab-a-1'].source);
        assert.deepEqual([image.state.generations,image.state.previews],[1,0],'SVG replay must not make model or preview API calls');
        assert.deepEqual(image.state.errors,[]);assert.deepEqual(image.state.unexpected,[]);
        console.log(JSON.stringify({origin,svg_image:true,no_script_execution:true,no_external_resources:true,model_calls:1,preview_posts:0}));
      }finally{await context.close();}
    }
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
