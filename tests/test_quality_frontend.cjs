const test=require('node:test');
const assert=require('node:assert/strict');
const quality=require('../src/new_api_cockpit/static/quality.js');

test('missing latency/rate is not zero, tables display two money decimals',()=>{
  assert.equal(quality.latency(null),'无数据');assert.equal(quality.rate(null),'无数据');
  assert.equal(quality.rate(0),'0%');assert.equal(quality.money('1.234567'),'1.23');assert.equal(quality.money(null),'—');
});
test('latency columns pair first-response and total duration at the same percentile',()=>{
  const row={frt_p50_ms:100,frt_p95_ms:900,duration_p50_ms:5000,duration_p95_ms:30000};
  assert.equal(quality.latencyPair(row,'p50'),'100ms / 5s');
  assert.equal(quality.latencyPair(row,'p95'),'900ms / 30s');
  assert.equal(quality.latencyPair({...row,frt_p50_ms:null},'p50'),'无数据 / 5s');
  assert.equal(quality.latencyPair({...row,duration_p95_ms:null},'p95'),'900ms / 无数据');
  assert.equal(quality.latencyPair({},'p50'),'无数据 / 无数据');
});
test('average token speed has two-decimal display and missing values are not zero',()=>{
  assert.equal(quality.tokensPerSecond(12.34567),'12.35');
  assert.equal(quality.tokensPerSecond(null),'无数据');
  assert.equal(quality.tokensPerSecond(undefined),'无数据');
  assert.equal(quality.tokensPerSecond(NaN),'无数据');
  assert.equal(quality.tokensPerSecond(0),'0');
  assert.equal(quality.throughput(48.7),'48.7 t/s');assert.equal(quality.throughput(null),'无数据');assert.equal(quality.throughput(NaN),'无数据');
  assert.equal(quality.healthRate(100),'100.00%');assert.equal(quality.healthRate(99.3),'99.30%');assert.equal(quality.healthRate(null),'无数据');
  assert.equal(quality.successTone(99.3),'quality-good');assert.equal(quality.successTone(75),'quality-warn');assert.equal(quality.successTone(50),'quality-bad');assert.equal(quality.successTone(null),'');
});
test('log links retain path only and use displayed inclusive-second period',()=>{
  const url=quality.logsURL({channel_id:7,model_name:'gpt'}, {start_ts:10,end_ts:21});
  assert.ok(url.startsWith('/usage-logs/common?'));
  const params=new URL(url,'https://example.test:24443').searchParams;
  assert.equal(params.get('channel'),'7');assert.equal(params.get('model'),'gpt');assert.equal(params.has('username'),false);
  assert.equal(params.get('startTime'),'10000');assert.equal(params.get('endTime'),'20000');
  assert.equal(quality.logsURL({model_name:'gpt'}, {start_ts:10,end_ts:21}),null);
});
test('channel page queries use fixed defaults and only the remaining filters',()=>{
  const params=quality.queryParams({start:'x',end:'y',mode:'upstream',stream:'stream',latency_scope:'all',scope_id:2},
    {model:new Set(['gpt']),channel_id:new Set(['7']),user:new Set(['fixture']),token_id:new Set(['8']),group:new Set(['vip']),tier:new Set(['high'])});
  for(const key of ['scope_id','group','tier','tag','mode','stream','latency_scope','user','token_id'])assert.equal(params.has(key),false);
  assert.equal(params.get('start'),'x');assert.equal(params.get('end'),'y');
  assert.equal(params.get('model'),'gpt');assert.equal(params.get('channel_id'),'7');
});

test('filter summaries use all, selected label or selected count like usage statistics',()=>{
  const choices=new Map([['gpt','gpt'],['7','upstream (ID 7)']]);
  assert.equal(quality.filterText('模型',new Set(),choices),'全部模型');
  assert.equal(quality.filterText('渠道',new Set(),choices),'全部渠道');
  assert.equal(quality.filterText('渠道',new Set(['7']),choices),'upstream (ID 7)');
  assert.equal(quality.filterText('模型',new Set(['gpt','claude']),choices),'已选 2 项');
});

test('model/channel checkboxes query immediately, preserve open menus and ignore stale results',async()=>{
  const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
  const root=path.join(__dirname,'../src/new_api_cockpit');
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.value='';this.open=false;this.textContent='';}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=items;}
    setAttribute(key,value){this[key]=value;}
    addEventListener(key,fn){(this.events[key]??=[]).push(fn);}
    querySelectorAll(selector){const nodes=this.children.filter(n=>n instanceof Element).flatMap(n=>[n,...n.querySelectorAll('*')]);return selector==='label'?nodes.filter(n=>n.tag==='label'):selector==='input[type="checkbox"]'?nodes.filter(n=>n.tag==='input'&&n.type==='checkbox'):nodes;}
    fire(name){for(const fn of this.events[name]||[])fn({target:this,preventDefault(){}});}
  }
  const html=fs.readFileSync(path.join(root,'templates/quality.html'),'utf8');
  const comparison=html.match(/<section class="page-panel">\s*<div class="page-heading"><h2>同模型渠道对比<\/h2><\/div>(.*?)<\/section>/s)[1];
  assert.equal(html.match(/id="quality-filters"/g).length,1);
  assert.ok(comparison.indexOf('id="quality-filters"')>=0);
  assert.ok(comparison.indexOf('id="quality-filters"')<comparison.indexOf('class="table-wrap"'));
  const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const start='2026-10-01T00:00:00',end='2026-10-01T23:59:59';
  elements.get('quality-start').value=start;elements.get('quality-end').value=end;
  const calls=[],pending=[];
  const document={getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[]};
  const context={document,URLSearchParams,AbortController,Intl,Date,console,fetch:(url,init)=>{calls.push({url,init});return new Promise(resolve=>pending.push(resolve));}};
  const result=amount=>({rows:[],top_models:[],totals:{request_count:0,success_rate:null,avg_duration_ms:null,avg_tokens_per_second:null,failure_count:0,amount},options:{models:['gpt','claude'],channels:[{channel_id:7,channel_name:'upstream',channel_status:1},{channel_id:8,channel_status:2},{channel_id:9,channel_status:3},{channel_id:10,channel_status:1,is_deleted:true}]},warnings:['must not render warning notes'],start,end,start_ts:1790784000,end_ts:1790870400,updated_at:'2026-10-10T00:00:00+08:00'});
  const settle=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
  const respond=async(index,amount='0')=>{
    const data=result(amount);
    if(amount==='44')data.rows=[{model_name:'gpt',channel_id:7,channel_name:'upstream',tag_value:'supplier',request_count:599,unique_requests:599,success_count:598,success_rate:99.83,outcome_coverage:100,failure_count:1,failure_codes:{429:1},frt_samples:3,duration_samples:3,tps_samples:598,tps_coverage:100,sample_insufficient:true,legacy_count:1,frt_p50_ms:300,duration_p50_ms:1000,frt_p95_ms:500,duration_p95_ms:2000,avg_tokens_per_second:20,amount}];
    if(amount==='44'){
      Object.assign(data.totals,{request_count:599,success_rate:99.83,avg_duration_ms:1500,avg_tokens_per_second:20,failure_count:1});
      data.top_models=[{model_name:'gpt',request_count:599,success_count:598,failure_count:1,success_rate:99.83}];
    }
    pending[index]({ok:true,json:async()=>data});await settle();
  };
  vm.runInNewContext(fs.readFileSync(path.join(root,'static/quality.js'),'utf8'),context);
  await respond(0);
  const pickers=elements.get('quality-filters').children;
  assert.equal(pickers.length,2);assert.deepEqual(pickers.map(p=>p.children[0].textContent),['全部模型','全部渠道']);
  assert.equal(elements.get('quality-rows').children[0].children[0].colSpan,10);
  assert.equal(html.includes('<th>状态</th>'),false);
  assert.equal(html.includes('<th>上游</th>'),false);
  assert.equal(html.match(/<th>请求次数<\/th>/g).length,1);
  assert.equal(html.includes('quality-detail'),false);
  assert.equal(html.includes('quality-export'),false);
  assert.equal(html.includes('<dialog'),false);
  assert.equal(html.includes('调用记录数'),false);
  for(const text of ['统计口径与数据限制','quality-warnings','quality-updated','个模型/渠道组合','单次最多 31 天'])assert.equal(html.includes(text),false);
  assert.equal(elements.get('quality-status').hidden,true);
  assert.equal(elements.get('quality-health-success').textContent,'无数据');
  assert.equal(elements.get('quality-health-latency').textContent,'无数据');
  assert.equal(elements.get('quality-health-throughput').textContent,'无数据');
  assert.equal(elements.get('quality-top-models').textContent,'暂无请求数据');
  assert.equal(html.match(/<thead><tr>(.*?)<\/tr><\/thead>/s)[1].match(/<th[ >]/g).length,10);
  const model=pickers[0],channel=pickers[1],body=model.children[1];model.open=true;
  assert.deepEqual(channel.children[1].querySelectorAll('input[type="checkbox"]').map(n=>n.value),['7']);
  const checkbox=body.querySelectorAll('input[type="checkbox"]').find(n=>n.value==='gpt');
  const search=body.children[0];search.value='gpt';search.fire('input');
  assert.equal(calls.length,1);assert.equal(body.querySelectorAll('label').filter(n=>!n.hidden).length,1);
  // Filters affect the displayed period, not unsubmitted edits in the date inputs.
  elements.get('quality-start').value='2026-10-02T00:00:00';
  checkbox.checked=true;checkbox.fire('change');assert.equal(calls.length,2);
  assert.equal(elements.get('quality-health-latency').textContent,'—');
  assert.equal(elements.get('quality-top-models').children.length,0);
  let params=new URL(calls[1].url,'https://example.test').searchParams;
  assert.equal(params.get('model'),'gpt');assert.equal(params.get('start'),start);
  await respond(1);assert.equal(model.open,true);
  assert.equal(body.querySelectorAll('input[type="checkbox"]').find(n=>n.value==='gpt'),checkbox);
  checkbox.checked=false;checkbox.fire('change');assert.equal(calls.length,3);
  const channelCheck=channel.children[1].querySelectorAll('input[type="checkbox"]')[0];
  channelCheck.checked=true;channelCheck.fire('change');assert.equal(calls.length,4);
  assert.equal(calls[2].init.signal.aborted,true);
  params=new URL(calls[3].url,'https://example.test').searchParams;
  assert.equal(params.get('channel_id'),'7');assert.equal(params.has('model'),false);
  for(const key of ['user','token_id'])assert.equal(params.has(key),false);
  await respond(3,'44');await respond(2,'99');
  assert.equal(elements.get('quality-metrics').children.at(-1).children[1].textContent,'44.00');
  assert.equal(elements.get('quality-metrics').children.length,3);
  assert.equal(elements.get('quality-health-success').textContent,'99.83%');
  assert.equal(elements.get('quality-health-latency').textContent,'1.5s');
  assert.equal(elements.get('quality-health-throughput').textContent,'20 t/s');
  assert.equal(elements.get('quality-health')['aria-busy'],'false');
  const models=elements.get('quality-top-models').children;assert.equal(models.length,1);
  assert.equal(models[0].children[0].textContent,'gpt');assert.equal(models[0].children[1].children[1].textContent,'99.83%');
  assert.equal(elements.get('quality-status').hidden,true);assert.equal(elements.get('quality-status').textContent,'');
  const cells=elements.get('quality-rows').children[0].children;
  assert.equal(cells.length,10);
  assert.equal(cells[0].textContent,'gpt');assert.equal(cells[1].textContent,'upstream (ID 7)');
  assert.equal(cells.some(cell=>cell.textContent==='supplier'),false);
  assert.equal(cells[2].textContent,'599');assert.equal(cells[5].textContent,'300ms / 1s');assert.equal(cells[7].textContent,'20');
  for(const i of [2,3,5,6,7])assert.equal(cells[i].children.length,0);
  assert.equal(cells[4].children[0].textContent,'429 1次');
  assert.equal(elements.get('quality-metrics').children[0].children[0].textContent,'请求次数');
  assert.equal(cells[9].children.length,1);
  const link=cells[9].children[0];assert.equal(link.tag,'a');assert.equal(link.textContent,'查看日志');
  assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');
  const logParams=new URL(link.href,'https://example.test').searchParams;
  assert.equal(logParams.get('model'),'gpt');assert.equal(logParams.get('channel'),'7');
  assert.equal(logParams.get('startTime'),'1790784000000');assert.equal(logParams.get('endTime'),'1790870399000');
  assert.equal(calls.every(call=>call.url.startsWith('/cockpit/api/quality/summary?')),true);
  // A selected channel disabled in New API disappears from the menu and is not cached.
  elements.get('quality-query').fire('submit');assert.equal(calls.length,5);
  const disabled=result('50');disabled.options.channels[0].channel_status=2;
  pending[4]({ok:true,json:async()=>disabled});await settle();
  assert.equal(calls.length,6);
  assert.equal(new URL(calls[5].url,'https://example.test').searchParams.has('channel_id'),false);
  assert.equal(channel.children[0].textContent,'全部渠道');
  assert.equal(channel.children[1].querySelectorAll('input[type="checkbox"]').length,0);
  const empty=result('0');empty.options.channels=[];
  pending[5]({ok:true,json:async()=>empty});await settle();
  assert.equal(elements.get('quality-metrics').children.at(-1).children[1].textContent,'0.00');
  assert.equal(elements.get('quality-health-success').textContent,'无数据');
  // Actual query failures remain visible instead of silently showing old/zero health.
  elements.get('quality-query').fire('submit');
  pending[6]({ok:false,json:async()=>({error:'查询超时，请缩小时间范围'})});await settle();
  assert.equal(elements.get('quality-status').hidden,false);
  assert.equal(elements.get('quality-status').textContent,'查询超时，请缩小时间范围');
});
