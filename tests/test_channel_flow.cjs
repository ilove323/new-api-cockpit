/* Synthetic DOM/API fixtures: no browser account, database or provider calls. */
const test=require('node:test');
const assert=require('node:assert/strict');
const create=require('../src/new_api_cockpit/static/channel-flow.js');

class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.ownText='';this.events={};this.dataset={};this.style={};this.attrs={};}
  set textContent(value){this.ownText=String(value);this.children=[];}
  get textContent(){return this.ownText+this.children.map(el=>el.textContent).join('');}
  append(...elements){this.children.push(...elements);}
  replaceChildren(...elements){this.ownText='';this.children=elements;}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(type,fn){this.events[type]=fn;}
}
const range={start:'2026-10-01T00:00:00',end:'2026-10-01T23:59:59'};
const nextRange={...range,end:'2026-10-02T23:59:59'};
const document={createElement:tag=>new Element(tag)};
const fixture=(bounds=range)=>({...bounds,rows:[
  {channel_id:1,channel_name:'渠道 <script>bad()</script>',amount:'2',request_count:4,user_count:2,users:[
    {user_id:12,username:'same-name',display_name:'<b>不执行</b>',amount:'1.5',percentage:'75',request_count:3},
    {user_id:28,username:'same-name',display_name:'',amount:'0.5',percentage:'25',request_count:1},
  ]},
  {channel_id:2,channel_name:'无消费渠道',amount:'0',request_count:0,user_count:0,users:[]},
  {channel_id:3,channel_name:'零金额渠道',amount:'0',request_count:1,user_count:1,users:[
    {user_id:36,username:'zero',display_name:'',amount:'0',percentage:null,request_count:1},
  ]},
]});
const find=(parent,cls)=>parent.children.flatMap(el=>[(el.className||'').split(' ').includes(cls)?el:null,...find(el,cls)]).filter(Boolean);
function harness(){
  const pending=[],calls=[],container=new Element();
  const flow=create({document,request:(url,options)=>{calls.push({url,options});return new Promise(resolve=>pending.push(resolve));}});
  return {flow,calls,pending,container,respond:(index,data=fixture())=>pending[index]({ok:true,json:async()=>data})};
}

test('all channel/user amounts and within-channel shares render safely, including no usage',async()=>{
  const h=harness(),request=h.flow.show(h.container,range);h.respond(0);await request;
  assert.equal(h.calls.length,1);
  const url=new URL(h.calls[0].url,'https://fixture.invalid');
  assert.equal(url.pathname,'/cockpit/api/statistics/channel-flow');
  assert.deepEqual([...url.searchParams.keys()],['start','end']);
  assert.equal(url.searchParams.get('start'),range.start);
  assert.equal(find(h.container,'channel-flow-channel').length,3);
  const users=find(h.container,'channel-flow-user');assert.equal(users.length,3);
  assert.deepEqual(users.map(row=>row.dataset.userId),['12','28','36']);
  assert.deepEqual(find(h.container,'bar').map(bar=>bar.style.width),['75%','25%','0%']);
  assert.deepEqual(find(h.container,'channel-flow-percentage').map(el=>el.textContent),['75.00%','25.00%','—']);
  assert.match(h.container.textContent,/合计 ¥ 2\.00/);
  assert.match(h.container.textContent,/所选时间内暂无消费/);
  assert.match(h.container.textContent,/<script>bad\(\)<\/script>/);
  assert.equal(find(h.container,'bar-label')[0].title,'same-name（#12） · <b>不执行</b>');
  assert.equal(h.container.attrs['aria-busy'],'false');
  assert.equal(h.container.innerHTML,undefined);
  await h.flow.show(h.container,range);assert.equal(h.calls.length,1,'Reopening the tab reuses the queried range');
});

test('pending requests deduplicate and hidden results never replace another ranking',async()=>{
  const h=harness(),first=h.flow.show(h.container,range),second=h.flow.show(h.container,range);
  assert.equal(first,second);assert.equal(h.calls.length,1);
  h.flow.hide();h.container.textContent='模型消费';h.respond(0);await first;
  assert.equal(h.container.textContent,'模型消费');
  await h.flow.show(h.container,range);assert.match(h.container.textContent,/合计 ¥ 2\.00/);
  assert.equal(h.calls.length,1);
});

test('a new query aborts old work and cannot display a stale response',async()=>{
  const h=harness(),old=h.flow.show(h.container,range);h.flow.reset();
  assert.equal(h.calls[0].options.signal.aborted,true);
  const current=h.flow.show(h.container,nextRange);h.respond(1,fixture(nextRange));await current;
  h.respond(0,{...fixture(),rows:[]});await old;
  assert.equal(find(h.container,'channel-flow-channel').length,3);
  h.flow.reset();const refresh=h.flow.show(h.container,nextRange);h.respond(2,fixture(nextRange));await refresh;
  assert.equal(h.calls.length,3,'An explicit query can refresh the same range once');
});

test('returning to a cached range cannot be overwritten by another in-flight range',async()=>{
  const h=harness(),first=h.flow.show(h.container,range);h.respond(0);await first;
  const other=h.flow.show(h.container,nextRange);await h.flow.show(h.container,range);
  h.respond(1,{...fixture(nextRange),rows:[]});await other;
  assert.equal(find(h.container,'channel-flow-channel').length,3);
});

test('errors require manual retry; empty catalogs and mismatched ranges are explicit',async()=>{
  const h=harness(),failed=h.flow.show(h.container,range);
  h.pending[0]({ok:false,json:async()=>({error:'查询超过限制'})});await failed;
  assert.match(h.container.textContent,/查询超过限制/);assert.equal(h.calls.length,1);
  const retry=h.container.children.find(el=>el.tag==='button').events.click();h.respond(1,{...fixture(),rows:[]});await retry;
  assert.match(h.container.textContent,/当前没有启用渠道/);assert.equal(h.calls.length,2);
  h.flow.reset();const mismatch=h.flow.show(h.container,range);h.respond(2,fixture(nextRange));await mismatch;
  assert.match(h.container.textContent,/时间范围不一致/);
  assert.equal(h.container.attrs['aria-busy'],'false');assert.equal(h.calls.length,3);
});
