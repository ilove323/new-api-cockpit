/* Offline model guide: real template/assets, synthetic account and clipboard only.
 * NODE_PATH=<playwright modules> COCKPIT_BROWSER_EXECUTABLE=<chromium> node tests/browser/manual-regression.cjs
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const manual=require('../../src/new_api_cockpit/static/manual-guides.js');
const root=path.resolve(__dirname,'../..'),staticRoot=path.join(root,'src/new_api_cockpit/static');
const origin='https://manual.fixture.test',key='sk-offline-fixture-only';
const rendered=spawnSync(process.env.COCKPIT_PYTHON||path.join(root,'.venv/bin/python'),['-c',
  'from flask import render_template,g\nfrom new_api_cockpit.app import app\n'
  +'with app.test_request_context("/cockpit/manual"):\n'
  +' g.current_user={"id":12,"username":"fixture","role":1,"session_id":"fixture-session","expires_at":4102444800}\n'
  +' print(render_template("manual.html",site_name="模型使用离线检查"))'],
  {cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},encoding:'utf8'});
assert.equal(rendered.status,0,rendered.stderr);
const mime={'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2','.webp':'image/webp'};
async function verify(browser,viewport){
  const context=await browser.newContext({viewport}),page=await context.newPage(),calls=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.fixtureClipboard=text;}}}));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());assert.equal(url.origin,origin,'No external network is allowed');
    if(url.pathname==='/cockpit/manual')return route.fulfill({contentType:'text/html',body:rendered.stdout});
    if(url.pathname==='/cockpit/static/auth.js')return route.fulfill({contentType:'text/javascript',body:'window.CockpitAuth={ensure:async()=>{}};'});
    if(url.pathname.startsWith('/cockpit/static/')){
      const file=path.resolve(staticRoot,decodeURIComponent(url.pathname.slice('/cockpit/static/'.length)));
      assert(file.startsWith(staticRoot+path.sep)&&fs.existsSync(file));
      return route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
    }
    if(url.pathname.startsWith('/cockpit/api/manual/')){
      calls.push(url.pathname);
      if(url.pathname.endsWith('/tokens'))return route.fulfill({json:{tokens:[{id:1,name:'fixture',key_masked:'sk-***',group:'default',usable:true,unlimited_quota:true}]}});
      if(url.pathname.endsWith('/models'))return route.fulfill({json:{models:[{id:'fixture-model'}]}});
      if(url.pathname.endsWith('/key'))return route.fulfill({json:{key}});
    }
    if(url.pathname==='/favicon.ico')return route.fulfill({status:204});
    throw new Error('Unexpected request: '+url.pathname);
  });
  try{
    await page.goto(origin+'/cockpit/manual');
    await page.locator('#manual-token').selectOption('1');
    await page.locator('#manual-model').selectOption('fixture-model');
    assert.equal(await page.locator('#manual-tools option').count(),15);
    const before=calls.length;let cases=0;
    for(const guide of manual.guides){
      await page.locator('#manual-tools').selectOption(guide.id);
      for(const variant of guide.variants||[{id:''}]){
        if(variant.id)await page.locator('#manual-variant').selectOption(variant.id);
        for(const platform of ['macos','windows','linux']){
          await page.locator('#manual-platform').selectOption(platform);
          for(const protocol of manual.resolve(guide.id,{variant:variant.id,platform}).protocols){
            await page.locator('#manual-protocol').selectOption(protocol);
            const expected=manual.build(guide.id,{origin,model:'fixture-model',variant:variant.id,platform,protocol});
            assert.equal(await page.locator('.manual-step-example').count(),expected.length);
            assert.equal(await page.locator('.manual-step-example > .manual-copy-actions > button:disabled').count(),0);
            assert.ok(!(await page.locator('body').innerText()).includes(key));
            assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${guide.id}/${platform}/${protocol}: page overflow`);
            cases++;
          }
        }
      }
    }
    assert.equal(cases,114);assert.equal(calls.length,before,'Tool/OS/protocol changes must not request upstream');
    await page.locator('#manual-tools').selectOption('deepseek-harness');
    await page.locator('#manual-protocol').selectOption('responses');
    const fields=page.locator('#manual-example-1 .manual-field-row');
    await fields.filter({has:page.locator('dt',{hasText:'基础 URL'})}).getByRole('button').click();
    assert.equal(await page.evaluate(()=>window.fixtureClipboard),origin+'/v1');assert.equal(calls.length,before);
    await fields.filter({has:page.locator('dt',{hasText:'API 密钥'})}).getByRole('button').click();
    await page.waitForFunction(expected=>window.fixtureClipboard===expected,key);
    assert.equal(calls.length,before+1);assert.ok(!(await page.content()).includes(key));
    if(process.env.COCKPIT_MANUAL_SCREENSHOTS){
      fs.mkdirSync(process.env.COCKPIT_MANUAL_SCREENSHOTS,{recursive:true});
      await page.evaluate(()=>window.scrollTo(0,0));
      await page.screenshot({path:path.join(process.env.COCKPIT_MANUAL_SCREENSHOTS,`manual-${viewport.width}.png`)});
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({offline:true,width:viewport.width,clients:15,cases,field_copy:true,key_reads:1,no_extra_queries:true,no_overflow:true}));
  }finally{await context.close();}
}
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.COCKPIT_BROWSER_EXECUTABLE?{executablePath:process.env.COCKPIT_BROWSER_EXECUTABLE}:{})});
  try{for(const viewport of [{width:1440,height:1100},{width:390,height:900}])await verify(browser,viewport);}
  finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
