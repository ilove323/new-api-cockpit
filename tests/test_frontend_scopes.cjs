/* No browser or third-party dependencies. Run: node --test tests/test_frontend_scopes.cjs */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'../src/new_api_statistics');
const read=file=>fs.readFileSync(path.join(root,file),'utf8');
const html=read('templates/index.html');
class Element {
  constructor(){this.children=[];this.dataset={};this.attrs={};this.events={};this.value='';this.checked=false;this.disabled=false;this.textContent='';this.classList={toggle(){},remove(){}};}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,fn){(this.events[k]??=[]).push(fn);}
  async fire(k){for(const fn of this.events[k]||[])await fn({preventDefault(){}});}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  focus(){}
}
const rows=[{id:1,kind:'all',tag_value:''},{id:3,kind:'tag',tag_value:'<img src=x onerror=alert(1)>'},{id:2,kind:'ungrouped',tag_value:''}];
const state=(enabled=false)=>({configured:true,settings:{enabled,budget:100,threshold:20,start_month:'2026-01',version:1},state:{remaining:10,current_amount:30,archived_amount:60,checked_at:'2026-09-22T10:00:00+08:00'},valid:true,alerts:[{remaining:10,threshold:20,updated_at:'2026-09-22T10:00:00+08:00'}],months:[{month:'2026-08',amount:60}]});
function harness(){
  const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const events={},calls=[];
  const ctx={URL,URLSearchParams,AbortController,DOMException,Event,console,Number,Date,Option:class extends Element {constructor(label,value){super();this.textContent=label;this.value=value;}},
    location:{origin:'https://example.test',href:'https://example.test/statistics/?dev=2',search:'?dev=2'},history:{replaceState(){}},
    document:{getElementById:id=>elements.get(id),createElement:()=>new Element(),querySelectorAll:selector=>selector==='#scope-tabs button'?elements.get('scope-tabs').children:[]},
    addEventListener:(name,fn)=>(events[name]??=[]).push(fn),dispatchEvent:event=>{for(const fn of events[event.type]||[])fn(event);},
    fetch:async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>url==='/statistics/api/scopes'?{rows:rows.map(r=>({...r}))}:state()};},
    $:id=>elements.get(id),number:n=>String(n),cell:(tr,value)=>{const el=new Element();el.textContent=value;tr.append(el);return el;}};
  ctx.window=ctx;vm.createContext(ctx);vm.runInContext(read('static/scopes.js'),ctx);
  const scope=vm.runInContext('Scope',ctx);
  return {ctx,scope,elements,calls,run:code=>vm.runInContext(code,ctx),balance:()=>vm.runInContext(read('static/balance.js'),ctx)};
}
const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
test('scope tabs are safely built and URLs preserve other filters',async()=>{
  const h=harness();await h.scope.init();
  assert.deepEqual(h.elements.get('scope-tabs').children.map(e=>e.textContent),['全部',rows[1].tag_value,'未分组']);
  const u=new URL(h.scope.url('/statistics/api/usage?group=auto&dev=2'),h.ctx.location.origin);
  assert.equal(u.searchParams.get('scope_id'),'1');assert.equal(u.searchParams.get('group'),'auto');
  await h.elements.get('scope-tabs').children[1].fire('click');assert.equal(h.scope.current.id,3);
});
test('old response body rejected after switching scope, including A-B-A',async()=>{
  const h=harness();await h.scope.init();let release;
  h.ctx.fetch=async()=>({ok:true,json:()=>new Promise(r=>release=r)});
  const response=await h.scope.request('/statistics/api/usage');const body=response.json();
  await h.elements.get('scope-tabs').children[1].fire('click');await h.elements.get('scope-tabs').children[0].fire('click');
  release({rows:[]});await assert.rejects(body,e=>e.name==='AbortError');
});
test('scope switches abort old HTTP requests',async()=>{
  const h=harness();await h.scope.init();await h.scope.request('/statistics/api/usage');const signal=h.calls.at(-1).options.signal;
  await h.elements.get('scope-tabs').children[1].fire('click');assert.equal(signal.aborted,true);
});
test('disabled monitor retains monthly view but never checks or shows stale alert',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  await h.elements.get('balance-bell').fire('click');
  assert.ok(h.elements.get('balance-months').children.length);assert.equal(h.elements.get('balance-alert-list').children.length,0);
  assert.equal(h.elements.get('balance-status').textContent,'监控未启用');
  assert.ok(!h.calls.some(c=>c.url.includes('/check')));
  assert.equal(h.elements.get('scope-tabs').children.length,3);
});
test('enabled monitor checks only current scope; notification API is global',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  h.ctx.fetch=async(url,options={})=>{h.calls.push({url,options});return {ok:true,json:async()=>state(true)};};
  await h.elements.get('balance-bell').fire('click');
  assert.ok(h.calls.some(c=>c.url==='/statistics/api/balance/check?scope_id=1'));
  await h.run("balanceRequest('/channel/test',balanceWrite('POST',{}))");
  assert.equal(h.calls.at(-1).url,'/statistics/api/balance/channel/test');
});
test('settings store enabled flag and read-only channels, without exclusions',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  h.elements.get('balance-enabled').checked=true;
  const body=h.run('balanceSettingsBody()');assert.equal(body.enabled,true);assert.ok(!('excluded_channel_ids' in body));
  h.run("renderUsageChannels([{channel_id:4,channel_name:'<script>',channel_status:1,deleted:false}])");
  const el=h.elements.get('usage-channel-options').children[0];assert.equal(el.children.length,2);assert.equal(el.children[0].textContent,'ID 4 · <script>');
});
test('no monitor configuration does not disable ledger tabs',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  h.ctx.fetch=async()=>({ok:true,json:async()=>({configured:false})});
  await h.elements.get('scope-tabs').children[1].fire('click');await settle();
  assert.equal(h.scope.current.id,3);assert.ok(h.elements.get('scope-tabs').children.every(b=>!b.disabled));
});
test('template static IDs, script order, scoped reports/export and global notifications contract',()=>{
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);
  for(const file of ['static/app.js','static/balance.js']){
    for(const match of read(file).matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.includes(match[1]),`missing DOM id ${match[1]}`);
  }
  const app=read('static/app.js');assert.ok(!app.includes("fetch('/statistics/api/usage"));
  assert.ok(app.includes("Scope.url('/statistics/api/export?"));
  assert.ok(!app.includes('balance-enabled'));assert.ok(app.includes("DOMContentLoaded"));
  assert.ok(html.indexOf("filename='scopes.js'")<html.indexOf("filename='app.js'"));
  assert.match(html,/id="balance-enabled" type="checkbox"/);assert.ok(!html.includes('usage-channels-all'));
});
test('old balance settings save cannot reopen dialogs in the new ledger',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  h.run('balanceVersion=1');h.elements.get('balance-save').disabled=false;
  let release;
  const before=h.ctx.fetch;
  h.ctx.fetch=async(url,options={})=>options.method==='PUT'?{ok:true,json:()=>new Promise(resolve=>release=resolve)}:before(url,options);
  const pending=h.elements.get('balance-form').fire('submit');await settle();assert.equal(typeof release,'function');
  await h.elements.get('scope-tabs').children[1].fire('click');await settle();release({version:2});await pending;
  assert.equal(h.elements.get('balance-alerts').open,false);assert.equal(h.run('balanceVersion'),null);
});
test('scope switching preserves global notification form and settings',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  h.elements.get('channel-app-id').value='global-app';h.run('channelVersion=7;channelDirty=true');
  await h.elements.get('scope-tabs').children[1].fire('click');await settle();
  assert.equal(h.elements.get('channel-app-id').value,'global-app');assert.equal(h.run('channelVersion'),7);assert.equal(h.run('channelDirty'),true);
});
test('history overwrite explicitly warns about all-ledger impact',()=>{
  const balance=read('static/balance.js');assert.ok(balance.includes('其他账本的历史金额和余额也可能变化'));
  assert.ok(html.includes('以下仅展示当前账本的逐月金额对比，不代表全部影响范围'));
  assert.ok(html.includes('确认重建所有账本归档'));
});
test('disabling and saving monitor retains selected ledger and all navigation',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  await h.elements.get('scope-tabs').children[1].fire('click');await settle();
  h.run('balanceVersion=1');h.elements.get('balance-save').disabled=false;
  h.elements.get('balance-enabled').checked=false;
  await h.elements.get('balance-form').fire('submit');
  const put=h.calls.find(c=>c.options.method==='PUT');
  assert.equal(put.url,'/statistics/api/balance/settings?scope_id=3');assert.equal(JSON.parse(put.options.body).enabled,false);
  assert.equal(h.scope.current.id,3);
  assert.equal(h.elements.get('scope-tabs').children[1].attrs['aria-selected'],'true');
  assert.ok(h.elements.get('scope-tabs').children.every(tab=>!tab.disabled));
  assert.equal(h.elements.get('balance-bell').disabled,false);
  assert.equal(h.elements.get('balance-gear').disabled,false);
  await h.elements.get('balance-bell').fire('click');
  assert.ok(!h.calls.some(c=>c.url.includes('/check')));
  assert.equal(h.elements.get('balance-months').children[0].children[1].textContent,'¥ 60.00');
});
test('monthly archive tables format money without changing source values or non-table formatter',()=>{
  const h=harness();h.balance();
  h.run(`balanceData=${JSON.stringify({...state(),months:[{month:'2026-08',amount:1234.567891},{month:'2026-07',amount:0.0049}]})};renderBalance()`);
  const rows=h.elements.get('balance-months').children;
  assert.equal(rows[0].children[1].textContent,'¥ 1,234.57');
  assert.equal(rows[1].children[1].textContent,'¥ 0.00');
  assert.equal(h.run('balanceData.months[0].amount'),1234.567891);
  assert.equal(h.run('balanceMoney(1234.567891)'),'¥ 1234.567891');
});
test('history comparison table formats before, after and difference without rounding preview data',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  const row={month:'2026-08',before:1234.567891,after:1240.123456};
  h.ctx.confirm=()=>true;
  h.ctx.fetch=async()=>({ok:true,json:async()=>({rows:[row]})});
  await h.elements.get('balance-recalculate').fire('click');
  const cells=h.elements.get('balance-history-preview-rows').children[0].children;
  assert.deepEqual(cells.map(c=>c.textContent),['2026-08','¥ 1,234.57','¥ 1,240.12','¥ 5.56']);
  assert.equal(h.run('historyPreview.rows[0].before'),row.before);
  assert.equal(h.run('historyPreview.rows[0].after'),row.after);
});
test('delayed old balance read cannot overwrite new ledger monthly data',async()=>{
  const h=harness();h.balance();await h.scope.init();await settle();
  let release;const original=h.ctx.fetch;let once=true;
  h.ctx.fetch=async(url,options)=>{if(once){once=false;return {ok:true,json:()=>new Promise(r=>release=r)};}return original(url,options);};
  const pending=h.run('refreshBalance()');const rejected=assert.rejects(pending,e=>e.name==='AbortError');await settle();
  await h.elements.get('scope-tabs').children[1].fire('click');await settle();
  const old=state();old.months=[{month:'1900-01',amount:999}];release(old);await rejected;
  assert.equal(h.elements.get('balance-months').children[0].children[0].textContent,'2026-08');
  assert.ok(h.elements.get('balance-alerts-title').textContent.includes(rows[1].tag_value));
});
test('unconfigured monitoring fallback catalog still selects all and emits report scopechange',async()=>{
  const h=harness();h.balance();let reportEvents=0;h.ctx.addEventListener('scopechange',()=>reportEvents++);
  h.ctx.fetch=async url=>({ok:true,json:async()=>url==='/statistics/api/scopes'?{rows:[{id:1,kind:'all',tag_value:''}]}:{configured:false}});
  await h.scope.init();await settle();
  assert.equal(reportEvents,1);assert.equal(h.scope.current.kind,'all');
  assert.equal(h.scope.url('/statistics/api/export?start=2026-01-01'),'/statistics/api/export?start=2026-01-01&scope_id=1');
  assert.equal(h.elements.get('scope-tabs').children[0].disabled,false);
});
