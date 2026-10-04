const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const helpers=require('../src/new_api_cockpit/static/keys.js');
const vm=require('node:vm');
test('money cells always use two decimal places',()=>{assert.equal(helpers.money('1.234567'),'1.23');assert.equal(helpers.money(0),'0.00');assert.equal(helpers.money(null),'—');});
test('group semantics stay separate from channel tags',()=>{assert.equal(helpers.groupName(''),'跟随用户组');assert.equal(helpers.groupName('auto'),'自动分组');assert.equal(helpers.groupName('team-a'),'team-a');});
test('option search is literal, not fuzzy wildcard or regex',()=>{assert.equal(helpers.keywordMatch('ask_copy','sk'),true);assert.equal(helpers.keywordMatch('ask_copy','a.*'),false);assert.equal(helpers.keywordMatch('ask_copy','%'),false);});
test('group patch does not include balance or status values',()=>{assert.deepEqual(helpers.groupChange('b'),{group:'b'});});
test('history never displays credential fields',()=>{assert.deepEqual(helpers.safeHistory({password:'fixture',key:'fixture',access_token:'fixture',group:'b'}),{group:'b'});});

/* DOM-only interaction fixtures: no browser and no live mutations. */
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.dataset={};this.style={};this.value='';this.checked=false;this.open=false;this.hidden=false;this.disabled=false;this.textContent='';this.isConnected=true;this.classList={add(){},toggle(){}};}
  append(...items){for(const item of items)this.children.push(...(item.tag==='fragment'?item.children:[item]));}
  replaceChildren(...items){this.children=[];this.append(...items);}
  addEventListener(name,callback){(this.events[name]??=[]).push(callback);}
  setAttribute(name,value){this[name]=value;}
  get firstChild(){return this.children[0];}
  get lastChild(){return this.children.at(-1);}
  get options(){return this.children;}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  focus(){this.focused=true;}
  getBoundingClientRect(){return {top:200,bottom:228,left:400,right:550};}
  showModal(){this.open=true;}
  close(){this.open=false;for(const callback of this.events.close||[])callback({target:this});}
  async fire(name){const event={target:this,prevented:false,preventDefault(){this.prevented=true;}};for(const callback of this.events[name]||[])await callback(event);return event;}
  querySelectorAll(selector){const all=[];for(const child of this.children){all.push(child,...child.querySelectorAll(selector));}return all.filter(n=>selector==='input'?n.tag==='input':selector==='[name]'?Boolean(n.name):selector==='input[type=checkbox]:checked'?n.tag==='input'&&n.type==='checkbox'&&n.checked:selector==='input[type=checkbox]'?n.tag==='input'&&n.type==='checkbox':selector===`[data-id="${n.dataset.id}"]`);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
async function harness(){
  const html=fs.readFileSync(require.resolve('../src/new_api_cockpit/templates/keys.html'),'utf8');
  const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const tabs=[...html.matchAll(/data-tab="([^"]+)"/g)].map(m=>{const node=new Element('button');node.dataset.tab=m[1];return node;});
  for(const [id,values] of [['user-status-options',[1,2]],['token-status-options',[1,2,3,4]]])for(const value of values){const input=new Element('input');input.type='checkbox';input.value=String(value);input.checked=id==='token-status-options'||value===1;elements.get(id).append(input);}
  const calls=[];let handler=()=>undefined;
  const row={id:10,user_id:2,username:'alice',display_name:'Alice',user_group:'a',name:'ask_copy',masked_key:'abcd********wxyz',token_group:'a',status:1,effective_status:1,quota_yuan:'1.2345',remain_quota_yuan:'2.3456',used_quota_yuan:'0',token_count:1,expired_time:-1};
  const user={id:2,username:'alice',display_name:'Alice',user_group:'a',status:1,quota_yuan:'1.2345',used_quota_yuan:'0',token_count:1,remark:'',tokens:[row]};
  const context={document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment'),querySelectorAll:selector=>selector==='[data-tab]'?tabs:[]},confirm:()=>true,console,Date,Number,Promise,URLSearchParams,window:{innerWidth:1280,innerHeight:800,addEventListener(){}},navigator:{clipboard:{writeText:async()=>{}}},fetch:async(url,init={})=>{const call={url,init,body:init.body?JSON.parse(init.body):undefined};calls.push(call);let data=await handler(call);if(data===undefined)data=url.endsWith('/options')?{groups:['a','b'],persistence:true}:url.includes('/query/')?{rows:[user],total:1,total_keys:1}:url.endsWith('/groups')?{group:'a',user_status:1,available_groups:['a','b']}:{rows:[]};return {ok:true,json:async()=>data};}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../src/new_api_cockpit/static/keys.js'),'utf8'),context);await settle();
  return {elements,tabs,calls,context,row,user,setHandler(fn){handler=fn;}};
}
test('combined list loads read-only, uses enabled users and all KEY states, formats money',async()=>{const h=await harness();assert.equal(h.calls.length,2);assert.equal(h.calls[1].url,'/cockpit/keys/api/query/grouped');assert.deepEqual(h.tabs.map(n=>n.dataset.tab),[]);assert.deepEqual(h.calls[1].body.user_statuses,[1]);assert.deepEqual(h.calls[1].body.token_statuses,[1,2,3,4]);const tr=h.elements.get('management-rows').firstChild;assert.ok(!tr.firstChild.children.some(n=>n.className==='user-meta user-money'));assert.equal(tr.children[7].textContent,'2.35');assert.match(h.elements.get('page-info').textContent,/1 个用户 \/ 1 个 KEY/);});
test('list search remains available and preserves filters without standalone lookup controls',async()=>{
  const h=await harness();h.elements.get('management-keyword').value='ask_copy';
  for(const id of ['user-group-options','token-group-options'])h.elements.get(id).querySelectorAll('input[type=checkbox]').find(input=>input.value==='a').checked=true;
  await h.elements.get('management-search').fire('submit');await settle();
  const call=h.calls.at(-1);assert.equal(call.url,'/cockpit/keys/api/query/grouped');assert.equal(call.init.method,'POST');
  assert.equal(call.body.search,'ask_copy');assert.deepEqual(call.body.user_groups,['a']);assert.deepEqual(call.body.token_groups,['a']);
  assert.deepEqual(call.body.user_statuses,[1]);assert.deepEqual(call.body.token_statuses,[1,2,3,4]);
  for(const id of ['find-key','key-search-dialog','key-search-form','search-full-key'])assert.equal(h.elements.has(id),false,id);
  assert.equal(h.elements.has('new-key'),true);assert.ok(!h.calls.some(c=>c.url.includes('ask_copy')));
});
test('full KEY reveal still requires confirmation and clears plaintext on dialog close',async()=>{
  const h=await harness();h.setHandler(c=>c.url.endsWith('/token/10/action')?{result:{key:'fixture-secret'}}:undefined);
  const reveal=h.elements.get('management-rows').firstChild.lastChild.children[3];
  h.context.confirm=()=>false;await reveal.fire('click');assert.equal(h.elements.get('reveal-dialog').open,false);
  h.context.confirm=()=>true;await reveal.fire('click');
  assert.deepEqual(h.calls.at(-1).body,{action:'reveal',changes:{}});assert.equal(h.elements.get('revealed-key').value,'fixture-secret');
  h.elements.get('reveal-dialog').close();assert.equal(h.elements.get('revealed-key').value,'');assert.equal(h.elements.get('revealed-key').type,'password');
  assert.ok(!h.calls.some(c=>c.url.includes('fixture-secret')));
});
test('group keyword filter is literal and does not trigger a data mutation',async()=>{const h=await harness();const group=h.elements.get('user-group-options');const search=group.firstChild;search.value='a.*';await search.fire('input');assert.ok(group.children.slice(1).every(n=>n.hidden));assert.equal(h.calls.length,2);});
test('KEY amounts allow fractional currency and edit window clears fields',async()=>{const h=await harness();await h.elements.get('new-key').fire('click');const amount=h.elements.get('edit-fields').querySelectorAll('[name]').find(n=>n.name==='amount_yuan');assert.equal(amount.step,'any');assert.equal(amount.min,'0');h.elements.get('edit-dialog').close();assert.equal(h.elements.get('edit-fields').children.length,0);});
test('batch preview is separate from mutation and keeps execution results visible',async()=>{const h=await harness();await h.elements.get('select-filtered').fire('click');h.elements.get('batch-target-group').value='b';let waves=0;h.setHandler(c=>c.url.endsWith('/groups/preview')?{operation_id:'fixture-op',count:6,rows:[{id:10,username:'alice',before_group:'a',after_group:'b'}]}:c.url.endsWith('/apply')?{done:++waves===2,results:Array.from({length:waves===1?5:1},(_,i)=>({id:10+i,state:'success',message:''}))}:undefined);await h.elements.get('batch-preview').fire('click');assert.equal(waves,0);assert.equal(h.elements.get('batch-dialog').open,true);await h.elements.get('batch-apply').fire('click');assert.equal(waves,2);assert.equal(h.elements.get('batch-dialog').open,true);assert.equal(h.elements.get('batch-rows').firstChild.lastChild.textContent,'成功');assert.match(h.elements.get('batch-progress').textContent,/6 个结果/);});
test('first user column spans only its own KEY rows, zero-KEY users retain a placeholder',async()=>{const h=await harness();const two={...h.user,token_count:2,tokens:[h.row,{...h.row,id:11}]};const none={...h.user,id:3,username:'empty-user',tokens:[],token_count:0};h.setHandler(c=>c.url.includes('/query/')?{rows:[two,none],total:2,total_keys:2}:undefined);await h.elements.get('management-refresh').fire('click');await settle();const rows=h.elements.get('management-rows').children;assert.equal(rows.length,3);assert.equal(rows[0].firstChild.rowSpan,2);assert.equal(rows[0].children.length,11);assert.equal(rows[1].children.length,10);assert.equal(rows[2].firstChild.rowSpan,1);assert.equal(rows[2].children[1].colSpan,10);assert.equal(rows[2].children[1].textContent,'暂无 KEY');assert.deepEqual(rows.map(n=>n.dataset.userId),[2,2,3]);});
test('KEY selection selects child IDs, ignores no-KEY rows and keeps total KEY count separate',async()=>{const h=await harness();h.setHandler(c=>c.url.includes('/query/')?{rows:[{...h.user,token_count:2,tokens:[h.row,{...h.row,id:11}]},{...h.user,id:3,tokens:[],token_count:0}],total:25,total_keys:70}:c.url.endsWith('/groups/preview')?{operation_id:'fixture-op',count:2,rows:[]}:undefined);await h.elements.get('management-refresh').fire('click');await settle();await h.elements.get('management-head').firstChild.children[1].firstChild.fire('change');assert.equal(h.elements.get('token-selection-count').textContent,'已选 0 个 KEY');const check=h.elements.get('management-head').firstChild.children[1].firstChild;check.checked=true;await check.fire('change');assert.equal(h.elements.get('token-selection-count').textContent,'已选 2 个 KEY');await h.elements.get('batch-preview').fire('click');const payload=h.calls.filter(c=>c.url.endsWith('/groups/preview')).at(-1).body;assert.deepEqual(payload.token_ids,[10,11]);assert.equal(payload.all_filtered,false);await h.elements.get('select-filtered').fire('click');assert.equal(h.elements.get('token-selection-count').textContent,'已选全部筛选结果（70 个 KEY）');});
test('token page has only KEY actions; new KEY binds owner and edit binds KEY ID',async()=>{
  const h=await harness();const owner=h.elements.get('management-rows').firstChild.firstChild;
  const actions=owner.children.find(n=>n.className==='user-actions');
  assert.deepEqual(actions.children.map(n=>n.textContent),['新增 KEY']);
  assert.equal(h.elements.has('new-user'),false);assert.equal(h.elements.has('history-panel'),false);
  await actions.children[0].fire('click');
  assert.equal(h.elements.get('edit-fields').querySelectorAll('[name]').find(n=>n.name==='user_id').value,2);
  h.elements.get('edit-dialog').close();
  h.setHandler(c=>c.url.endsWith('/token/10')?{...h.row,group:'a',available_groups:['a']}:undefined);
  const keyActions=h.elements.get('management-rows').firstChild.lastChild;await keyActions.children[1].fire('click');
  assert.equal(h.calls.at(-1).url,'/cockpit/keys/api/token/10');
});

const groupPickerFor=h=>h.elements.get('management-rows').firstChild.children[5].firstChild;
async function openGroups(h){const picker=groupPickerFor(h);picker.open=true;await picker.fire('toggle');return picker;}
test('only the group cell is interactive; its lazy menu reads owner-allowed groups',async()=>{
  const h=await harness();const tr=h.elements.get('management-rows').firstChild;
  assert.equal(tr.children[4].textContent,h.row.masked_key);assert.equal(tr.children[4].children.length,0);
  assert.equal(h.elements.get('management-head').firstChild.children[5].textContent,'分组');
  assert.equal(groupPickerFor(h).firstChild.textContent,'a');
  h.setHandler(c=>c.url.endsWith('/groups')?{group:'a',user_status:1,available_groups:['b']}:undefined);
  const picker=await openGroups(h);const menu=picker.children[1];
  assert.equal(h.calls.at(-1).url,'/cockpit/keys/api/token/10/groups');assert.equal(h.calls.at(-1).init.method,undefined);
  const buttons=menu.children.filter(n=>n.tag==='button');assert.deepEqual(buttons.map(n=>n.dataset.group),['','auto','b']);
  assert.ok(!h.calls.some(c=>c.url.endsWith('/action')));
  const search=menu.children.find(n=>n.tag==='input');search.value='b';await search.fire('input');assert.deepEqual(buttons.filter(n=>!n.hidden).map(n=>n.dataset.group),['b']);
});
test('group selection requires one confirmation; cancel or selecting the current group never writes',async()=>{
  const h=await harness();const picker=await openGroups(h);const options=picker.children[1].children.filter(n=>n.tag==='button');
  const current=options.find(n=>n.dataset.group==='a');assert.equal(current.disabled,true);await current.fire('click');assert.equal(h.elements.get('key-group-dialog').open,false);
  await options.find(n=>n.dataset.group==='b').fire('click');assert.equal(picker.open,false);assert.equal(h.elements.get('key-group-dialog').open,true);assert.match(h.elements.get('key-group-summary').textContent,/a → b/);
  assert.ok(!h.calls.some(c=>c.url.endsWith('/action')));h.elements.get('key-group-dialog').close();await h.elements.get('key-group-form').fire('submit');assert.ok(!h.calls.some(c=>c.url.endsWith('/action')));
});
test('confirmed group switch sends only the group patch once and refreshes the list',async()=>{
  const h=await harness();const picker=await openGroups(h);await picker.children[1].children.find(n=>n.tag==='button'&&n.dataset.group==='b').fire('click');
  h.setHandler(c=>{if(c.url.endsWith('/action')){h.row.token_group='b';return {ok:true};}});
  await h.elements.get('key-group-form').fire('submit');await h.elements.get('key-group-form').fire('submit');
  const actions=h.calls.filter(c=>c.url.endsWith('/action'));assert.equal(actions.length,1);assert.deepEqual(actions[0].body,{action:'group',changes:{group:'b'}});
  assert.equal(actions[0].init.headers['X-Management-Action'],'confirm');assert.equal(h.elements.get('key-group-dialog').open,false);assert.equal(groupPickerFor(h).firstChild.textContent,'b');
});
test('in-flight or ambiguous group switch blocks duplicate submits and never retries',async()=>{
  const h=await harness();const picker=await openGroups(h);await picker.children[1].children.find(n=>n.tag==='button'&&n.dataset.group==='b').fire('click');
  let rejectRequest;h.setHandler(c=>c.url.endsWith('/action')?new Promise((_,reject)=>{rejectRequest=reject;}):undefined);
  const request=h.elements.get('key-group-form').fire('submit');await settle();await h.elements.get('key-group-form').fire('submit');
  assert.equal((await h.elements.get('key-group-dialog').fire('cancel')).prevented,true);assert.equal(h.elements.get('key-group-cancel').disabled,true);
  rejectRequest(new Error('fixture disconnect'));await request;await h.elements.get('key-group-form').fire('submit');
  assert.equal(h.calls.filter(c=>c.url.endsWith('/action')).length,1);assert.equal(h.elements.get('key-group-save').disabled,true);assert.equal(h.elements.get('key-group-cancel').disabled,false);assert.match(h.elements.get('key-group-error').textContent,/不会重复发送/);
});
test('closed menus ignore late group responses and disabled owners cannot select a group',async()=>{
  const h=await harness();let resolveRead;h.setHandler(c=>c.url.endsWith('/groups')?new Promise(resolve=>{resolveRead=resolve;}):undefined);
  const picker=groupPickerFor(h);picker.open=true;const loading=picker.fire('toggle');await settle();picker.open=false;resolveRead({group:'a',user_status:1,available_groups:['b']});await loading;
  assert.ok(!picker.children[1].children.some(n=>n.tag==='button'));
  h.setHandler(c=>c.url.endsWith('/groups')?{group:'a',user_status:2,available_groups:['b']}:undefined);await openGroups(h);assert.match(picker.children[1].firstChild.textContent,/已禁用/);assert.ok(!picker.children[1].children.some(n=>n.tag==='button'));
});
