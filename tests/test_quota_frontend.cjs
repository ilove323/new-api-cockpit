/* DOM fixtures only: no browser and no real quota mutations. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../src/new_api_statistics');
const html=fs.readFileSync(path.join(root,'templates/quota.html'),'utf8');
const js=fs.readFileSync(path.join(root,'static/quota.js'),'utf8');
test('mode selector centers a compact text-and-arrow group without wide blank padding',()=>{
  const css=fs.readFileSync(path.join(root,'static/quota.css'),'utf8');
  assert.ok(!css.includes('padding-inline: 32px'));
  assert.ok(css.includes('width: calc(4em + 41px)'));
  assert.ok(css.includes('label:has(#mode){justify-items:center}'));
});
test('statistics and quota links and entry redirects preserve the current origin',()=>{
  const index=fs.readFileSync(path.join(root,'templates/index.html'),'utf8');
  const nginx=fs.readFileSync(path.join(__dirname,'../nginx.conf.example'),'utf8');
  assert.match(index,/class="quota-nav" href="\/quota\/"/);
  assert.match(html,/href="\/statistics\/"/);
  for(const route of ['statistics','quota']){
    const block=nginx.match(new RegExp(`location = /${route} \\{([^}]+)\\}`))[1];
    assert.match(block,/absolute_redirect off;/);
    assert.ok(block.includes(`return 302 /${route}/$is_args$args;`));
  }
});
class Element{
  constructor(){this.children=[];this.events={};this.value='';this.checked=false;this.textContent='';this.open=false;this.classList={toggle(){},contains(){return false;}};}
  setAttribute(){}
  addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
  async fire(name){for(const fn of this.events[name]||[])await fn({target:this});}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  checkValidity(){return true;}
}
const sample=[
  {id:1,username:'alice',display_name:'',user_group:'default',status:1,quota_yuan:'1',used_quota_yuan:'0'},
  {id:2,username:'bob',display_name:'',user_group:'default',status:2,quota_yuan:'1',used_quota_yuan:'0'},
  {id:3,username:'carol',display_name:'',user_group:'disabled-only',status:2,quota_yuan:'1',used_quota_yuan:'0'},
];
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
async function harness(){
  const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  let rows=sample.map(r=>({...r}));
  const ctx={document:{getElementById:id=>elements.get(id),createElement:()=>new Element(),createTextNode:text=>({textContent:text})},
    fetch:async()=>({ok:true,json:async()=>({rows})})};
  vm.createContext(ctx);vm.runInContext(js,ctx);await settle();
  return {ctx,elements,run:code=>vm.runInContext(code,ctx),ids:()=>JSON.parse(vm.runInContext('JSON.stringify([...selected])',ctx)),
    setRows:value=>{rows=value;},change:async(id,checked)=>{elements.get(id).checked=checked;await elements.get(id).fire('change');}};
}
test('default enabled status filters rows, group counts and batch selection',async()=>{
  const h=await harness();
  assert.equal(h.elements.get('status-enabled').checked,true);
  assert.equal(h.elements.get('status-disabled').checked,false);
  assert.equal(h.elements.get('users').children.length,1);
  assert.equal(h.elements.get('users').children[0].children[4].textContent,'启用');
  assert.equal(h.elements.get('group-options').children.length,1);
  assert.equal(h.elements.get('group-options').children[0].children[1].textContent,'default（1 人）');
  h.run("selectedGroups.add('default')");
  await h.elements.get('select-groups').fire('click');
  assert.deepEqual(h.ids(),[1]);
});
test('quota list and preview table show two decimals without rounding messages or request amounts',async()=>{
  const h=await harness();
  h.setRows([{...sample[0],quota_yuan:'1234.567891',used_quota_yuan:'0.0049'}]);await h.run('load()');
  const cells=h.elements.get('users').children[0].children;
  assert.equal(cells[5].textContent,'1,234.57');assert.equal(cells[6].textContent,'0.00');
  assert.equal(h.run('users[0].quota_yuan'),'1234.567891');
  h.run('selected.add(1)');h.elements.get('amount').value='1.123456';h.elements.get('mode').value='add';
  let payload;
  h.ctx.fetch=async(url,options)=>{payload=JSON.parse(options.body);return {ok:true,json:async()=>({mode:'add',amount_yuan:'1.123456',users:[{id:1,username:'alice',before_yuan:'1234.567891',estimated_after_yuan:'1235.691347'}]})};};
  await h.elements.get('preview').fire('click');
  const preview=h.elements.get('preview-rows').children[0].children;
  assert.equal(preview[1].textContent,'1,234.57');assert.equal(preview[2].textContent,'1,235.69');
  assert.equal(payload.amount_yuan,'1.123456');
  assert.ok(h.elements.get('confirm-summary').textContent.includes('1.123456'));
});
test('disabled users require explicit status opt-in and are removed on opt-out',async()=>{
  const h=await harness();await h.change('status-disabled',true);
  assert.equal(h.elements.get('users').children.length,3);
  assert.equal(h.elements.get('users').children[1].children[4].textContent,'禁用');
  h.run("selectedGroups.add('default')");await h.elements.get('select-groups').fire('click');
  assert.deepEqual(h.ids(),[1,2]);
  await h.change('status-disabled',false);
  assert.deepEqual(h.ids(),[1]);
  await h.elements.get('select-groups').fire('click');assert.deepEqual(h.ids(),[1]);
});
test('disabled-only and empty status selections are respected by select-all',async()=>{
  const h=await harness();await h.change('status-disabled',true);await h.change('status-enabled',false);
  h.elements.get('select-all').checked=true;await h.elements.get('select-all').fire('change');
  assert.deepEqual(h.ids(),[2,3]);
  await h.change('status-disabled',false);
  assert.deepEqual(h.ids(),[]);assert.equal(h.elements.get('users').children.length,0);
  assert.equal(h.elements.get('preview').disabled,true);
});
test('refresh prunes accounts that become disabled under the enabled filter',async()=>{
  const h=await harness();h.run('selected.add(1)');
  h.setRows(sample.map(r=>({...r,status:2})));await h.run('load()');
  assert.deepEqual(h.ids(),[]);assert.equal(h.elements.get('users').children.length,0);
});
test('a pending preview cannot reopen after its status filter changes',async()=>{
  const h=await harness();h.run('selected.add(1)');h.elements.get('amount').value='10';h.elements.get('mode').value='add';
  let release;h.ctx.fetch=async()=>({ok:true,json:()=>new Promise(resolve=>{release=resolve;})});
  const request=h.elements.get('preview').fire('click');await settle();
  await h.change('status-enabled',false);release({mode:'add',amount_yuan:'10',users:[]});await request;
  assert.equal(h.elements.get('confirm-dialog').open,false);
  assert.equal(h.elements.get('preview').disabled,true);
});

function confirmed(h,ids){
  h.run(`pending={user_ids:${JSON.stringify(ids)},mode:'add',amount_yuan:'380'};for(const id of pending.user_ids)resultCells.set(id,document.createElement('td'));`);
  h.elements.get('confirm-dialog').showModal();
}
function waveResponse(wave,failedId){return {completed:!wave.includes(failedId),results:wave.map(id=>({id,ok:id!==failedId,message:id===failedId?'失败':''}))};}
test('more than 100 selected users stay enabled and execute in waves of at most five',async()=>{
  const h=await harness();const ids=Array.from({length:103},(_,i)=>i+1);
  const rows=ids.map(id=>({...sample[0],id}));h.setRows(rows);await h.run('load()');
  h.run("selectedGroups.add('default')");await h.elements.get('select-groups').fire('click');
  assert.equal(h.ids().length,103);assert.equal(h.elements.get('preview').disabled,false);
  confirmed(h,ids);const waves=[];
  h.ctx.fetch=async(url,options)=>{
    if(url==='/quota/api/users')return {ok:true,json:async()=>({rows})};
    const wave=JSON.parse(options.body).user_ids;waves.push(wave);
    return {ok:true,json:async()=>waveResponse(wave)};
  };
  await h.elements.get('apply').fire('click');
  assert.equal(waves.length,21);assert.equal(waves.at(-1).length,3);
  assert.deepEqual(waves.flat(),ids);assert.ok(waves.every(w=>w.length<=5));
  assert.match(h.elements.get('execution-progress').textContent,/完成：103 人/);
  assert.equal(h.elements.get('apply').disabled,true);
  await h.elements.get('apply').fire('click');assert.equal(waves.length,21);
});
test('the next wave waits for the current HTTP response; duplicate clicks do not resend',async()=>{
  const h=await harness(),ids=Array.from({length:11},(_,i)=>i+1);confirmed(h,ids);
  let release;const waves=[];
  h.ctx.fetch=async(url,options)=>{
    if(url==='/quota/api/users')return {ok:true,json:async()=>({rows:sample})};
    const wave=JSON.parse(options.body).user_ids;waves.push(wave);
    return {ok:true,json:waves.length===1?()=>new Promise(resolve=>{release=()=>resolve(waveResponse(wave));}):async()=>waveResponse(wave)};
  };
  const request=h.elements.get('apply').fire('click');await settle();
  assert.equal(waves.length,1);await h.elements.get('apply').fire('click');assert.equal(waves.length,1);
  release();await request;assert.deepEqual(waves.map(w=>w.length),[5,5,1]);
});
test('a failed wave records all concurrent outcomes and leaves later users unstarted',async()=>{
  const h=await harness(),ids=Array.from({length:12},(_,i)=>i+1);confirmed(h,ids);const waves=[];
  h.ctx.fetch=async(url,options)=>{
    if(url==='/quota/api/users')return {ok:true,json:async()=>({rows:sample})};
    const wave=JSON.parse(options.body).user_ids;waves.push(wave);
    return {ok:true,json:async()=>waveResponse(wave,7)};
  };
  await h.elements.get('apply').fire('click');assert.equal(waves.length,2);
  assert.match(h.elements.get('execution-progress').textContent,/成功 9 人，后续 2 人未发起/);
  assert.equal(h.run('resultCells.get(8).textContent'),'成功');
  assert.match(h.run('resultCells.get(7).textContent'),/失败/);
  assert.equal(h.run('resultCells.get(11).textContent'),'未发起');
});
test('transport failure marks the sent wave uncertain and never retries',async()=>{
  const h=await harness();confirmed(h,[1,2,3,4,5,6]);let calls=0;
  h.ctx.fetch=async(url)=>{if(url==='/quota/api/users')return {ok:true,json:async()=>({rows:sample})};calls++;throw new Error('断开');};
  await h.elements.get('apply').fire('click');assert.equal(calls,1);
  assert.match(h.run('resultCells.get(1).textContent'),/结果待核对/);
  assert.equal(h.run('resultCells.get(6).textContent'),'未发起');
});
test('incomplete per-user response stops dispatch instead of treating the wave as successful',async()=>{
  const h=await harness();confirmed(h,[1,2,3,4,5,6]);let calls=0;
  h.ctx.fetch=async(url)=>{
    if(url==='/quota/api/users')return {ok:true,json:async()=>({rows:sample})};
    calls++;return {ok:true,json:async()=>({completed:true,results:[{id:1,ok:true}]})};
  };
  await h.elements.get('apply').fire('click');assert.equal(calls,1);
  assert.match(h.run('resultCells.get(1).textContent'),/结果待核对/);
  assert.equal(h.run('resultCells.get(6).textContent'),'未发起');
});
for(const change of ['amount','mode','clear','select-all','select-groups','deselect-groups','user-checkbox'])test(`late quota preview is discarded after ${change}`,async()=>{
  const h=await harness();h.run("selected.add(1);selectedGroups.add('default');render()");h.elements.get('amount').value='10';h.elements.get('mode').value='add';
  let release;h.ctx.fetch=async()=>({ok:true,json:()=>new Promise(resolve=>{release=resolve;})});
  const request=h.elements.get('preview').fire('click');await settle();
  if(change==='amount'){h.elements.get('amount').value='99';await h.elements.get('amount').fire('input');}
  else if(change==='mode'){h.elements.get('mode').value='subtract';await h.elements.get('mode').fire('change');}
  else if(change==='clear')await h.elements.get('clear-selection').fire('click');
  else if(change==='select-all')await h.change('select-all',false);
  else if(change==='user-checkbox'){const checkbox=h.elements.get('users').children[0].children[0].children[0];checkbox.checked=false;await checkbox.fire('change');}
  else await h.elements.get(change).fire('click');
  release({mode:'add',amount_yuan:'10',users:[]});await request;
  assert.equal(h.elements.get('confirm-dialog').open,false);
  assert.equal(h.run('pending'),null);
});
test('outdated preview errors do not erase a newer confirmation',async()=>{
  const h=await harness();h.run('selected.add(1)');h.elements.get('amount').value='10';h.elements.get('mode').value='add';
  let reject;h.ctx.fetch=()=>new Promise((_,r)=>{reject=r;});
  const old=h.elements.get('preview').fire('click');await settle();
  h.elements.get('amount').value='99';await h.elements.get('amount').fire('input');
  h.ctx.fetch=async()=>({ok:true,json:async()=>({mode:'add',amount_yuan:'99',users:[]})});
  await h.elements.get('preview').fire('click');reject(new Error('obsolete'));await old;
  assert.equal(h.elements.get('confirm-dialog').open,true);
  assert.equal(h.run('pending.amount_yuan'),'99');
});
