/* DOM fixtures only: table formatting, column choices and optional failure queries. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../src/new_api_cockpit/static');
const html=fs.readFileSync(path.join(root,'../templates/index.html'),'utf8');
class Element{
  constructor(){this.children=[];this.dataset={};this.attrs={};this.events={};this.checked=false;this.disabled=false;this.value='';this.textContent='';this.style={setProperty(){}};this.classList={toggle(){},add(){}};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(type,fn){(this.events[type]??=[]).push(fn);}
  async fire(type){for(const fn of this.events[type]||[])await fn({preventDefault(){}});}
  setAttribute(key,value){this.attrs[key]=value;}
  querySelectorAll(selector){return descendants(this).filter(el=>selector==='label'?el.tag==='label':el.tag==='input'&&el.checked);}
}
function descendants(el){return el.children.flatMap(child=>[child,...descendants(child)]);}
function harness({search='',preferences}={}){
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const columns=new Map(),headers=new Map(),controls=[];
  for(const match of html.matchAll(/<(input|th|button)\b([^>]*)>/g)){
    const attrs=Object.fromEntries([...match[2].matchAll(/([\w-]+)="([^"]*)"/g)].map(m=>[m[1],m[2]]));
    if(!attrs['data-column-toggle']&&!attrs['data-column']&&!Object.keys(attrs).some(k=>/data-(detail-mode|model-mode|preset|ranking)/.test(k)))continue;
    const el=attrs.id?get(attrs.id):new Element();el.tag=match[1];el.attrs=attrs;
    el.checked=/\bchecked\b/.test(match[2]);el.hidden=/\bhidden\b/.test(match[2]);
    for(const [key,value] of Object.entries(attrs))if(key.startsWith('data-'))el.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;
    if(attrs['data-column-toggle'])columns.set(attrs['data-column-toggle'],el);
    else if(attrs['data-column'])headers.set(attrs['data-column'],el);
    else controls.push(el);
  }
  const allCells=()=>[...headers.values(),...descendants(get('rows')),...descendants(get('totals'))];
  const select=selector=>{
    if(selector.startsWith('[data-column-toggle]'))return [...columns.values()].filter(el=>!selector.includes(':checked')||el.checked).filter(el=>!selector.includes(':not(:disabled)')||!el.disabled);
    if(selector==='#usage-table [data-column]')return allCells().filter(el=>el.dataset.column);
    if(selector==='#usage-table .empty')return allCells().filter(el=>el.className==='empty');
    const filter=selector.match(/^#(\w+)-options input:checked$/);
    if(filter)return get(filter[1]+'-options').querySelectorAll('input:checked');
    const control=selector.match(/^\[data-(detail-mode|model-mode|preset|ranking)\]$/);
    if(control)return controls.filter(el=>Object.hasOwn(el.attrs,'data-'+control[1]));
    return [];
  };
  const storage=new Map(preferences?[['new-api-cockpit.visible-columns.v2',JSON.stringify(preferences)]]:[]),requests=[];
  get('start').value='2026-09-01T00:00:00';get('end').value='2026-09-30T23:59:59';
  const defaultResponse=url=>{
    const u=new URL(url,'https://fixture.invalid'),params=u.searchParams;
    let rows=[{...row}];
    if(params.get('include_failures')==='1')rows=[{...row,failure_count:3,failure_codes:{429:3}},{...row,user_id:2,username:'failure-only',model_name:'failed-model',request_count:0,amount:0,total_tokens:0,input_tokens:0,output_tokens:0,cache_read_tokens:0,cache_write_tokens:0,failure_count:1,failure_codes:{502:1}}];
    if(u.pathname.endsWith('/tokens'))rows=[{token_id:1,token_name:'key'}];
    if(u.pathname.endsWith('/groups'))rows=[{group_name:'default'}];
    return {ok:true,json:async()=>({start:params.get('start'),end:params.get('end'),rows,totals:{duration_seconds:60},updated_at:'2026-10-04T12:00:00+08:00'})};
  };
  let respond=defaultResponse;
  const scope={epoch:1,current:{id:7},guard(epoch){if(epoch!==this.epoch)throw new Error('stale scope');},
    url(url){const u=new URL(url,'https://fixture.invalid');u.searchParams.set('scope_id',this.current.id);return u.pathname+u.search;},
    async request(url){url=this.url(url);requests.push(url);return respond(url);}};
  const context={URLSearchParams,Scope:scope,
    window:{location:{search,assign:url=>{context.exportURL=url;}},addEventListener(){}},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
    document:{getElementById:get,createElement:tag=>{const el=new Element();el.tag=tag;return el;},createTextNode:text=>{const el=new Element();el.textContent=text;return el;},querySelectorAll:select,querySelector:selector=>select(selector)[0]||null},
    hideMoneyTooltip(){},bindMoneyTooltip:(cell,formula)=>{cell.formula=formula;},
    rowMoneyFormula:row=>row.amount,totalMoneyFormula:(rows,amount)=>amount,
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root,'app.js'),'utf8'),context);
  return {elements,columns,headers,storage,requests,context,defaultResponse,respond:fn=>{respond=fn;},run:code=>vm.runInContext(code,context),select:(id,value)=>{
    const input=get(id+'-options').querySelectorAll('label').flatMap(el=>el.children).find(el=>el.tag==='input'&&el.value===value);
    assert.ok(input,`${id}: ${value}`);input.checked=true;
  }};
}
const row={user_id:1,username:'tester',model_name:'gpt',tier_name:'low',token_id:1,token_name:'key',
  request_count:2,total_tokens:1000000,input_tokens:800000,output_tokens:10,
  cache_read_tokens:199990,cache_write_tokens:0,group_ratio:3.456789,
  input_price:2,output_price:10.123456,cache_price:0.0049,write_price:null,amount:1234.567891};
test('table currency formatter fixes two decimals, including zeros, rounding and missing values',()=>{
  const h=harness();
  for(const [value,expected] of [[2,'2.00'],[0,'0.00'],[0.0049,'0.00'],[0.005,'0.01'],[-12.3456,'-12.35'],['1234.567891','1,234.57'],[null,'—']]){
    assert.equal(h.run(`tableMoney(${JSON.stringify(value)})`),expected);
  }
  assert.equal(h.run('tableMoney(undefined)'),'—');
  assert.equal(h.run('number(3.456789)'),'3.456789');
  assert.equal(h.run("priceDisplay({pricing_mode:'expression'},'input_price')"),'无法拆分');
});
test('usage rows, monetary prices, optional unit prices and footer use two decimals only',()=>{
  const h=harness();h.run(`snapshot={rows:[${JSON.stringify(row)}]};renderDetails()`);
  const cells=new Map(h.elements.get('rows').children[0].children.map(c=>[c.dataset.column,c]));
  for(const [column,expected] of Object.entries({input_price:'2.00',output_price:'10.12',cache_price:'0.00',write_price:'—',amount:'1,234.57',amount_per_million:'1,234.57',group_ratio:'3.456789',total_tokens:'1,000,000'})){
    assert.equal(cells.get(column).textContent,expected,column);
  }
  const footer=h.elements.get('totals').children[0].children.find(c=>c.dataset.column==='amount');
  assert.equal(footer.textContent,'1,234.57');
  assert.equal(cells.get('amount').formula(),row.amount);
  assert.equal(footer.formula(),row.amount);
  assert.equal(h.run('snapshot.rows[0].amount'),row.amount);
});
test('optional columns default off even with old dev URLs, and saved choices restore independently',async()=>{
  const optional=['cache_hit_rate','amount_per_million','failure_requests'];
  for(const search of ['', '?dev=1','?dev=2']){
    const h=harness({search});
    for(const column of optional){assert.equal(h.columns.get(column).checked,false);assert.equal(h.headers.get(column).hidden,true);}
    await h.run('query()');assert.ok(h.requests.every(url=>!url.includes('include_failures')&&!url.includes('dev=')));
  }
  const h=harness({preferences:['username','amount']});
  assert.equal(h.run('visibleColumnCount()'),2);
  for(const column of optional)assert.equal(h.columns.get(column).checked,false);
  h.columns.get('cache_hit_rate').checked=true;await h.columns.get('cache_hit_rate').fire('change');
  const saved=JSON.parse(h.storage.get('new-api-cockpit.visible-columns.v2'));
  const restored=harness({preferences:saved});
  assert.equal(restored.columns.get('cache_hit_rate').checked,true);
  assert.equal(restored.columns.get('failure_requests').checked,false);
});
test('computed column choices show row and total metrics without fetching or altering source values',async()=>{
  const h=harness();await h.run('query()');const count=h.requests.length;
  for(const column of ['cache_hit_rate','amount_per_million']){h.columns.get(column).checked=true;await h.columns.get(column).fire('change');}
  const cells=h.elements.get('rows').children[0].children;
  assert.equal(cells.find(c=>c.dataset.column==='cache_hit_rate').textContent,'20%');
  assert.equal(cells.find(c=>c.dataset.column==='amount_per_million').textContent,'1,234.57');
  for(const parent of ['rows','totals'])for(const column of ['cache_hit_rate','amount_per_million']){
    assert.equal(h.elements.get(parent).children[0].children.find(c=>c.dataset.column===column).hidden,false);
  }
  assert.equal(h.requests.length,count);assert.equal(h.run('snapshot.rows[0].amount'),row.amount);
  await h.elements.get('export').fire('click');
  const exported=new URL(h.context.exportURL,'https://fixture.invalid');
  assert.equal(exported.searchParams.get('scope_id'),'7');
  assert.equal(exported.searchParams.has('include_failures'),false);
  assert.equal(exported.searchParams.has('cache_hit_rate'),false);
});
test('failure column uses explicit flags on all queries and displayed dates, preserving filters and modes',async()=>{
  const h=harness();await h.run('query()');
  const metrics=Object.fromEntries(['amount','counts','tpm','rpm'].map(id=>[id,h.elements.get(id).textContent]));
  const charts=h.elements.get('chart').children.length;
  const start=h.run('snapshot.start'),end=h.run('snapshot.end');
  h.elements.get('start').value='2026-10-01T00:00:00';h.elements.get('end').value='2026-10-04T12:00:00';
  const before=h.requests.length,toggle=h.columns.get('failure_requests');
  toggle.checked=true;await toggle.fire('change');
  assert.equal(h.headers.get('failure_requests').hidden,false);
  assert.equal(h.elements.get('rows').children.length,2);
  assert.equal(h.elements.get('rows').children[0].children.find(c=>c.dataset.column==='failure_requests').textContent,'429 3次');
  assert.equal(h.elements.get('totals').children[0].children.find(c=>c.dataset.column==='failure_requests').textContent,'429 3次\n502 1次');
  for(const [id,value] of Object.entries(metrics))assert.equal(h.elements.get(id).textContent,value,id);
  assert.equal(h.elements.get('chart').children.length,charts);
  await h.run("setDetailMode('token');loadTokenDetails()");
  for(const [id,value] of [['user','tester'],['model','gpt'],['token','1'],['group','default']])h.select(id,value);
  await h.run("loadFilteredSelection()");h.run("setModelMode('summary');renderDetails()");
  const requests=h.requests.slice(before);
  for(const path of ['/usage','/usage/tokens','/usage/groups','/usage/by-token','/usage/by-selection'])assert.ok(requests.some(url=>new URL(url,'https://fixture.invalid').pathname.endsWith(path)),path);
  for(const url of requests){const params=new URL(url,'https://fixture.invalid').searchParams;
    assert.equal(params.get('include_failures'),'1');assert.equal(params.get('start'),start);assert.equal(params.get('end'),end);assert.equal(params.get('scope_id'),'7');assert.equal(params.has('dev'),false);
  }
  const off=h.requests.length;toggle.checked=false;await toggle.fire('change');
  assert.equal(h.headers.get('failure_requests').hidden,true);assert.equal(toggle.disabled,false);
  assert.ok(h.requests.slice(off).every(url=>!url.includes('include_failures')));
  assert.equal(h.run('detailMode'),'token');assert.equal(h.run('modelMode'),'summary');
  for(const [id,value] of [['user','tester'],['model','gpt'],['token','1'],['group','default']])assert.equal(h.run(`selectedFilterValues('${id}').has('${value}')`),true);
  assert.equal(h.elements.get('rows').children.length,1);
  assert.equal(h.run('snapshot.start'),start);assert.equal(h.elements.get('start').value,'2026-10-01T00:00:00');
  await h.elements.get('export').fire('click');assert.ok(!h.context.exportURL.includes('include_failures'));
});
test('all columns explicitly enables failures; empty rows keep their colspan in sync',async()=>{
  const h=harness();await h.run('query()');await h.elements.get('show-all-columns').fire('click');
  assert.ok([...h.columns.values()].every(column=>column.checked));assert.ok(h.requests.at(-1).includes('include_failures=1'));
  h.run('snapshot.rows=[];renderDetails()');
  const empty=h.elements.get('rows').children[0].children[0];assert.equal(empty.colSpan,h.columns.size);
  h.columns.get('cache_hit_rate').checked=false;await h.columns.get('cache_hit_rate').fire('change');
  assert.equal(empty.colSpan,h.columns.size-1);
});
test('failed diagnostic reload restores the prior column selection and data',async()=>{
  const h=harness();await h.run('query()');
  h.respond(()=>({ok:false,json:async()=>({error:'fixture failure'})}));
  const toggle=h.columns.get('failure_requests');toggle.checked=true;await toggle.fire('change');
  assert.equal(toggle.checked,false);assert.equal(toggle.disabled,false);assert.equal(h.headers.get('failure_requests').hidden,true);
  assert.equal(h.elements.get('status').textContent,'fixture failure');
  assert.equal(h.run('snapshot.rows[0].amount'),row.amount);
});
test('a delayed token response cannot overwrite the same-date snapshot after failure column changes',async()=>{
  const h=harness();await h.run('query()');let release;
  h.respond(url=>url.includes('/by-token?')?{ok:true,json:()=>new Promise(resolve=>{release=resolve;})}:h.defaultResponse(url));
  const pending=h.run('loadTokenDetails()');for(let i=0;i<5;i++)await Promise.resolve();assert.equal(typeof release,'function');
  const toggle=h.columns.get('failure_requests');toggle.checked=true;await toggle.fire('change');
  release({rows:[{...row,username:'stale'}]});await pending;
  assert.equal(h.run('tokenSnapshot'),null);
  assert.equal(h.run('snapshot.rows[0].failure_count'),3);
});
test('token detail and model-summary tables keep the same presentation-only formatting',()=>{
  for(const [detail,model] of [['token','model'],['summary','summary'],['token','summary']]){
    const h=harness();h.run(`detailMode='${detail}';modelMode='${model}';snapshot={rows:[${JSON.stringify(row)}]};tokenSnapshot=snapshot;renderDetails()`);
    const amount=h.elements.get('rows').children[0].children.find(c=>c.dataset.column==='amount');
    assert.equal(amount.textContent,'1,234.57');assert.equal(amount.formula(),row.amount);
  }
});
test('unknown cache counts propagate to table totals rather than pretending zero',()=>{
  const h=harness();h.run(`snapshot={rows:[${JSON.stringify({...row,cache_read_tokens:null,input_tokens:null,total_tokens:null})}],totals:{duration_seconds:60}};renderSummary(snapshot.rows);renderDetails()`);
  assert.equal(h.elements.get('cache_read_tokens').textContent,'—');
  assert.equal(h.elements.get('total_tokens').textContent,'—');
  assert.equal(h.elements.get('tpm').textContent,'—');
  const footer=h.elements.get('totals').children[0].children;
  assert.equal(footer.find(c=>c.dataset.column==='cache_read_tokens').textContent,'—');
  assert.equal(footer.find(c=>c.dataset.column==='amount').textContent,'1,234.57');
});
