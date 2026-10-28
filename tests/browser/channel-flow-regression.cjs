/* Offline statistics UI: channel tab, range refresh, caching and responsive layout.
 * NODE_PATH=<playwright modules> COCKPIT_BROWSER_EXECUTABLE=<chromium> node tests/browser/channel-flow-regression.cjs
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),staticRoot=path.join(root,'src/new_api_cockpit/static');
const rendered=spawnSync(process.env.COCKPIT_PYTHON||path.join(root,'.venv/bin/python'),['-c',
  'from flask import render_template,g\nfrom new_api_cockpit.app import app\n'
  +'with app.test_request_context("/cockpit/statistics/"):\n'
  +' g.current_user={"id":1,"username":"fixture-admin","role":100,"session_id":"fixture-session","expires_at":4102444800}\n'
  +' print(render_template("index.html",site_name="渠道分流离线验证",start="2026-10-01T00:00:00",end="2026-10-01T23:59:59"))'],
  {cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},encoding:'utf8'});
assert.equal(rendered.status,0,rendered.stderr);
const record={user_id:12,username:'账号甲',display_name:'',model_name:'fixture-model',request_count:4,amount:'2',total_tokens:100,input_tokens:50,output_tokens:50,cache_read_tokens:0,cache_write_tokens:0,group_ratio:'1',input_price:'2',output_price:'4',cache_price:'0.2',write_price:'2.5'};
const channelRows=[
  {channel_id:1,channel_name:'渠道 A',amount:'2',user_count:2,request_count:4,users:[
    {user_id:12,username:'账号甲',display_name:'',amount:'1.5',percentage:'75',request_count:3},
    {user_id:28,username:'历史用户乙',display_name:'',amount:'0.5',percentage:'25',request_count:1},
  ]},
  {channel_id:2,channel_name:'渠道 B',amount:'0',user_count:0,request_count:0,users:[]},
  {channel_id:3,channel_name:'渠道 C',amount:'0',user_count:1,request_count:1,users:[
    {user_id:36,username:'零金额用户',display_name:'',amount:'0',percentage:null,request_count:1},
  ]},
];
const mime={'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2'};

async function verify(browser,viewport){
  const context=await browser.newContext({viewport}),page=await context.newPage(),calls=[],errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());assert.equal(url.origin,'http://channel-flow.test');
    if(url.pathname==='/cockpit/statistics/')return route.fulfill({contentType:'text/html',body:rendered.stdout});
    if(url.pathname==='/cockpit/static/auth.js')return route.fulfill({contentType:'text/javascript',body:'window.CockpitAuth={ensure:async()=>{}};'});
    if(url.pathname==='/cockpit/static/balance.js')return route.fulfill({contentType:'text/javascript',body:'/* unrelated balance API is outside this fixture */'});
    if(url.pathname.startsWith('/cockpit/static/')){
      const file=path.resolve(staticRoot,decodeURIComponent(url.pathname.slice('/cockpit/static/'.length)));
      assert(file.startsWith(staticRoot+path.sep)&&fs.existsSync(file));
      return route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
    }
    if(url.pathname==='/cockpit/api/statistics/scopes')return route.fulfill({json:{rows:[{id:1,kind:'all',tag_value:''}]}});
    if(url.pathname==='/cockpit/api/statistics/channel-flow'){
      assert.deepEqual([...url.searchParams.keys()],['start','end']);calls.push(url);
      return route.fulfill({json:{start:url.searchParams.get('start'),end:url.searchParams.get('end'),rows:channelRows}});
    }
    if(url.pathname.startsWith('/cockpit/api/statistics/usage')){
      const rows=url.pathname.endsWith('/tokens')?[]:url.pathname.endsWith('/groups')?[]:[record];
      return route.fulfill({json:{start:url.searchParams.get('start'),end:url.searchParams.get('end'),rows,totals:{duration_seconds:86400},updated_at:'2026-10-01T23:59:59+08:00'}});
    }
    if(url.pathname==='/favicon.ico')return route.fulfill({status:204});
    throw new Error('Unexpected fixture request: '+url.pathname);
  });
  try{
    await page.goto('http://channel-flow.test/cockpit/statistics/');
    await page.waitForFunction(()=>document.getElementById('export').disabled===false);
    assert.equal(calls.length,0,'No channel query on ordinary statistics load');
    assert.deepEqual(await page.locator('[data-ranking]').allTextContents(),['模型消费','用户 Token','用户消费','渠道分流']);
    await page.locator('#tab-model').focus();await page.keyboard.press('End');
    await page.locator('.channel-flow-channel').nth(2).waitFor();
    assert.equal(calls.length,1);assert.equal(await page.locator('#chart').getAttribute('aria-labelledby'),'tab-channel-flow');
    assert.deepEqual(await page.locator('.channel-flow-percentage').allTextContents(),['75.00%','25.00%','—']);
    assert.match(await page.locator('#chart').innerText(),/所选时间内暂无消费/);
    assert.equal(await page.locator('.channel-flow-user').first().locator('.bar').evaluate(el=>el.style.width),'75%');
    await page.locator('#tab-model').click();await page.locator('#tab-channel-flow').click();
    assert.equal(calls.length,1,'Reopening the tab uses the existing result');
    // Native datetime-local inputs normalize a zero seconds field away.
    await page.locator('#start').fill('2026-10-02T00:00');
    await page.locator('#tab-amount').click();await page.locator('#tab-channel-flow').click();
    assert.equal(calls.length,1,'Unsubmitted dates do not replace the current report range');
    if(process.env.COCKPIT_CHANNEL_FLOW_SCREENSHOTS){
      fs.mkdirSync(process.env.COCKPIT_CHANNEL_FLOW_SCREENSHOTS,{recursive:true});
      await page.locator('.chart-section').screenshot({path:path.join(process.env.COCKPIT_CHANNEL_FLOW_SCREENSHOTS,`channel-flow-${viewport.width}.png`)});
    }
    await page.locator('#end').fill('2026-10-02T23:59:59');await page.locator('#submit').click();
    await page.waitForFunction(()=>document.querySelectorAll('.channel-flow-channel').length===3&&!document.getElementById('submit').disabled);
    assert.equal(calls.length,2);assert.equal(calls[1].searchParams.get('start'),'2026-10-02T00:00');
    assert.equal(calls[1].searchParams.get('end'),'2026-10-02T23:59:59');
    const overflow=await page.locator('#chart').evaluate(el=>el.scrollWidth>el.clientWidth+1);assert.equal(overflow,false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({offline:true,width:viewport.width,channels:3,channel_queries:2,tab_cache:true,range_refresh:true,zero_and_empty:true,no_horizontal_overflow:true}));
  }finally{await context.close();}
}

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.COCKPIT_BROWSER_EXECUTABLE?{executablePath:process.env.COCKPIT_BROWSER_EXECUTABLE}:{})});
  try{for(const viewport of [{width:1440,height:1100},{width:390,height:900}])await verify(browser,viewport);}
  finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
