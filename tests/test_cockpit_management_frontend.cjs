/* Functional DOM fixtures; no browser, production database or native mutations. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'../src/new_api_cockpit');
class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.textContent='';this.value='';this.checked=false;this.open=false;this.disabled=false;this.hidden=false;this.classList={toggle(){},add(){}};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(k,v){this[k]=v;}
  addEventListener(k,fn){(this.events[k]??=[]).push(fn);}
  showModal(){this.open=true;}
  close(){this.open=false;for(const fn of this.events.close||[])fn({target:this});}
  get firstChild(){return this.children[0];}
  get lastChild(){return this.children.at(-1);}
  checkValidity(){return true;}
  querySelectorAll(selector){const all=[];for(const c of this.children)if(c instanceof Element)all.push(c,...c.querySelectorAll(selector));return selector==='[name]'?all.filter(c=>c.name):all;}
  async fire(k){for(const fn of this.events[k]||[])await fn({target:this,preventDefault(){}});}
}
const settle=async()=>{for(let i=0;i<35;i++)await Promise.resolve();};
function environment(html){const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));const ctx={console,URLSearchParams,Date,Number,Promise,confirm:()=>true,document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text})}};ctx.window=ctx;vm.createContext(ctx);return {elements,ctx,run:file=>vm.runInContext(fs.readFileSync(path.join(root,'static/'+file),'utf8'),ctx)};}
async function users(){
  const h=environment(fs.readFileSync(path.join(root,'templates/users.html'),'utf8')+fs.readFileSync(path.join(root,'templates/partials/user-profile.html'),'utf8'));
  h.calls=[];h.handle=()=>undefined;let user={id:2,username:'alice',display_name:'Alice',user_group:'a',status:1,quota_yuan:'1.234567',used_quota_yuan:'2',token_count:2,remark:'fixture'};
  h.ctx.fetch=async(url,init={})=>{const c={url,init,body:init.body?JSON.parse(init.body):undefined};h.calls.push(c);let data=await h.handle(c);if(data===undefined)data=url.endsWith('/options')?{groups:['a','b'],persistence:true}:url==='/cockpit/users/api/users'?{rows:[user]}:url.endsWith('/user/2')?{...user,group:'a'}:{ok:true};return {ok:true,json:async()=>data};};
  h.run('user-profile.js');h.run('quota.js');await settle();return h;
}
test('user list includes quota, KEY link, remark and user-only actions in final column',async()=>{
  const h=await users(),cells=h.elements.get('users').firstChild.children;
  assert.equal(cells.length,10);assert.equal(cells[5].textContent,'1.23');assert.equal(cells[7].firstChild.href,'/cockpit/keys/?user_id=2');assert.equal(cells[8].textContent,'fixture');
  assert.deepEqual(cells[9].children.map(c=>c.textContent),['编辑','重置密码','禁用','删除']);
  assert.deepEqual(cells[9].children.map(c=>c['data-icon']),['pencil','key-round','power-off','trash-2']);
  assert.ok(cells[9].children.every(c=>c.className.includes('action-icon')&&c.title&&c['aria-label'].includes('alice')));
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/cockpit/users/api/users');
});
test('user edit sends whitelist profile fields, not quota, KEY or PAT fields',async()=>{
  const h=await users();await h.elements.get('users').firstChild.lastChild.children[0].fire('click');
  assert.equal(h.elements.get('user-profile-dialog').open,true);assert.equal(h.calls.at(-1).url,'/cockpit/users/api/user/2');
  const fields=h.elements.get('user-profile-fields').querySelectorAll('[name]');assert.deepEqual(fields.map(f=>f.name),['username','display_name','group','remark']);
  fields.find(f=>f.name==='username').value='alice-renamed';await h.elements.get('user-profile-form').fire('submit');
  const request=h.calls.find(c=>c.body);assert.equal(request.url,'/cockpit/users/api/user/2/action');assert.equal(request.init.headers['X-Management-Action'],'confirm');assert.equal(request.body.action,'edit');assert.equal(request.body.changes.username,'alice-renamed');
  assert.equal(h.elements.get('user-profile-fields').children.length,0);assert.equal(h.calls.at(-1).url,'/cockpit/users/api/users');
});
test('password reset validates confirmation and clears both password inputs on close',async()=>{
  const h=await users();await h.elements.get('users').firstChild.lastChild.children[1].fire('click');
  const fields=h.elements.get('user-profile-fields').querySelectorAll('[name]');fields[0].value='fixture-password';fields[1].value='wrong';
  await h.elements.get('user-profile-form').fire('submit');assert.ok(!h.calls.some(c=>c.body));assert.match(h.elements.get('user-profile-error').textContent,/不一致/);
  fields[1].value='fixture-password';await h.elements.get('user-profile-form').fire('submit');assert.equal(h.calls.find(c=>c.body).body.action,'password');assert.equal(h.elements.get('user-profile-fields').children.length,0);
});
test('ambiguous profile mutation cannot be resubmitted; quota execution blocks profile actions',async()=>{
  const h=await users();await h.elements.get('users').firstChild.lastChild.children[0].fire('click');
  let attempts=0;h.handle=c=>{if(c.body){attempts++;throw new Error('fixture disconnect');}};
  await h.elements.get('user-profile-form').fire('submit');await h.elements.get('user-profile-form').fire('submit');assert.equal(attempts,1);assert.equal(h.elements.get('user-profile-save').disabled,true);
  h.elements.get('user-profile-dialog').close();vm.runInContext('executing=true;render()',h.ctx);
  assert.ok(h.elements.get('users').firstChild.lastChild.children.every(c=>c.disabled));const before=h.calls.length;await h.ctx.UserProfiles.open(2);assert.equal(h.calls.length,before);
});
test('unified records list/details are paginated, read-only, two-decimal and safely rendered',async()=>{
  const h=environment(fs.readFileSync(path.join(root,'templates/operations.html'),'utf8'));const calls=[];
  h.ctx.fetch=async(url,init)=>{calls.push({url,init});const detail=url.includes('/records/schedule/7');return {ok:true,json:async()=>detail?{rows:[{target_type:'user',target_id:2,user_id:2,before_data:{username:'<img src=x>'},after_data:{amount_units:617283945,operation:'add'},state:'unknown',message:'请核对'}],next_after:2}:{rows:[{source:'schedule',id:'7',occurred_at:'2026-10-01T00:00:00+08:00',operator_name:'admin',action:'schedule.execute',parameters:{amount_units:617283945},state:'unknown',counts:{success:4,uncertain:1,pending:5}}],next_before:'fixture-cursor'} };};
  h.run('operations.js');await settle();const row=h.elements.get('records-rows').firstChild;assert.match(row.children[3].textContent,/1,234\.57/);assert.equal(row.children[4].textContent,'结果不明确');assert.equal(row.children[5].textContent,'4 / 0 / 1');assert.doesNotMatch(fs.readFileSync(path.join(root,'templates/operations.html'),'utf8'),/未发起/);
  await row.lastChild.firstChild.fire('click');const item=h.elements.get('records-items').firstChild;assert.match(item.children[3].textContent,/1,234\.57/);assert.match(item.children[2].textContent,/<img src=x>/);
  await h.elements.get('records-items-more').fire('click');assert.ok(calls.some(c=>c.url.endsWith('?after=2')));
  h.elements.get('records-dialog').close();await h.elements.get('records-next').fire('click');assert.ok(calls.some(c=>c.url.includes('before=fixture-cursor')));
  await h.elements.get('records-prev').fire('click');assert.equal(h.elements.get('records-page').textContent,'第 1 页');
  assert.ok(calls.every(c=>c.init.method===undefined&&!c.init.body));
  assert.doesNotMatch(fs.readFileSync(path.join(root,'static/operations.js'),'utf8'),/innerHTML\s*=/);
});
