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
  h.ctx.fetch=async(url,init)=>{calls.push({url,init});const detail=url.includes('/records/schedule/7'),user={id:2,username:'<img src=x>'};return {ok:true,json:async()=>detail?{rows:[{target_type:'user',target_id:2,user_id:2,target_user:user,before_data:{username:user.username},after_data:{amount_units:617283945,operation:'add'},state:'unknown',message:'请核对'}],next_after:2}:{rows:[{source:'schedule',id:'7',occurred_at:'2026-10-01T00:00:00+08:00',operator_name:'admin',action:'schedule.execute',parameters:{amount_units:617283945},target_users:[user],target_user_count:1,state:'unknown',counts:{success:4,uncertain:1,pending:5}}],next_before:'fixture-cursor'} };};
  h.run('operations.js');await settle();const row=h.elements.get('records-rows').firstChild;assert.match(row.children[3].textContent,/1,234\.57/);assert.equal(row.children[4].textContent,'结果不明确');assert.equal(row.children[5].textContent,'4 / 0 / 1');assert.doesNotMatch(fs.readFileSync(path.join(root,'templates/operations.html'),'utf8'),/未发起/);
  await row.lastChild.firstChild.fire('click');const item=h.elements.get('records-items').firstChild;assert.match(item.children[3].textContent,/1,234\.57/);assert.match(item.children[2].textContent,/<img src=x>/);assert.equal(item.children[1].textContent,'<img src=x>（ID 2）');
  await h.elements.get('records-items-more').fire('click');assert.ok(calls.some(c=>c.url.endsWith('?after=2')));
  h.elements.get('records-dialog').close();await h.elements.get('records-next').fire('click');assert.ok(calls.some(c=>c.url.includes('before=fixture-cursor')));
  await h.elements.get('records-prev').fire('click');assert.equal(h.elements.get('records-page').textContent,'第 1 页');
  assert.ok(calls.every(c=>c.init.method===undefined&&!c.init.body));
  assert.doesNotMatch(fs.readFileSync(path.join(root,'static/operations.js'),'utf8'),/innerHTML\s*=/);
});
test('all user, KEY and quota operation labels identify their targets in the list and details',async()=>{
  const h=environment(fs.readFileSync(path.join(root,'templates/operations.html'),'utf8'));const calls=[];
  const actions=[['user.edit','编辑用户'],['user.password','重置密码'],['user.enable','启用用户'],['user.disable','禁用用户'],['user.delete','删除用户'],['user.pat_create','补建用户 PAT'],['token.create','新增 KEY'],['token.edit','编辑 KEY'],['token.quota','KEY 额度调整'],['token.enable','启用 KEY'],['token.disable','禁用 KEY'],['token.delete','删除 KEY'],['token.reveal','查看 KEY'],['token.group','KEY 改组'],['token.batch_group','批量 KEY 改组'],['quota.add','增加用户额度'],['quota.subtract','减少用户额度'],['schedule.execute','定时额度执行']];
  const user={id:2,username:'alice'},fixtures=actions.map(([action],i)=>({source:'management',id:'fixture-'+i,operator_name:'admin',action,state:'completed',parameters:{},target_users:[user],target_user_count:1,counts:{success:1}}));
  fixtures.push(
    {source:'management',id:'fixture-deleted',operator_name:'admin',action:'token.group',state:'completed',parameters:{},target_users:[{id:7,username:''}],target_user_count:1,counts:{success:1}},
    {source:'management',id:'fixture-new',operator_name:'admin',action:'user.create',state:'completed',parameters:{},target_users:[{id:null,username:'new-user'}],target_user_count:1,counts:{success:1}}
  );
  h.ctx.fetch=async(url,init)=>{calls.push({url,init});const record=fixtures.find(r=>url.endsWith('/management/'+r.id));return {ok:true,json:async()=>record?{rows:[{target_type:record.action.startsWith('token.')?'token':'user',target_id:record.action.endsWith('.create')?0:record.action.startsWith('token.')?10:record.target_users[0].id,user_id:record.target_users[0].id,target_user:record.target_users[0],before_data:{},after_data:{target_user:record.target_users[0]},state:'success',message:''}],next_after:null}:{rows:fixtures,next_before:null}};};
  h.run('operations.js');await settle();const rows=h.elements.get('records-rows').children;
  for(const [i,[,label]] of actions.entries()){assert.equal(rows[i].children[1].textContent,'admin');assert.equal(rows[i].children[2].textContent,label+' · alice（ID 2）');await rows[i].lastChild.firstChild.fire('click');assert.equal(h.elements.get('records-title').textContent,label+' · alice（ID 2）');const item=h.elements.get('records-items').firstChild;assert.equal(item.children[1].textContent,'alice（ID 2）');assert.equal(item.children[3].textContent,'—');h.elements.get('records-dialog').close();}
  assert.equal(rows[actions.length].children[2].textContent,'KEY 改组 · 用户 ID 7');
  assert.equal(rows[actions.length+1].children[2].textContent,'新增用户 · new-user（ID 未返回）');
  await rows[actions.length+1].lastChild.firstChild.fire('click');const created=h.elements.get('records-items').firstChild;assert.equal(created.children[0].textContent,'用户 / ID 未返回');assert.equal(created.children[1].textContent,'new-user（ID 未返回）');
  assert.ok(calls.every(c=>c.init.method===undefined&&!c.init.body));
});
test('batch targets are summarized; rule operations name their groups rather than the executor',async()=>{
  const h=environment(fs.readFileSync(path.join(root,'templates/operations.html'),'utf8')),rules=[['schedule.create','新增定时规则'],['schedule.edit','编辑定时规则'],['schedule.enable','启用定时规则'],['schedule.disable','停用定时规则'],['schedule.delete','删除定时规则']],targetRule={id:9,groups:['team-a','team-b']};
  h.ctx.fetch=async url=>({ok:true,json:async()=>url.includes('/records/management/')?{rows:[{target_type:'quota_rule',target_id:9,user_id:null,target_rule:targetRule,before_data:{groups:['team-a']},after_data:{groups:['team-a','team-b']},state:'success',message:''}],next_after:null}:{rows:[
    {source:'management',id:'batch',operator_name:'admin',action:'quota.add',target_users:[{id:2,username:'alice'},{id:3,username:'bob'},{id:4,username:'carol'}],target_user_count:8,parameters:{}},
    ...rules.map(([action],i)=>({source:'management',id:'rule-'+i,operator_name:'admin',action,target_users:[],target_user_count:0,target_rule:targetRule,parameters:{}}))
  ],next_before:null}});
  h.run('operations.js');await settle();const rows=h.elements.get('records-rows').children;
  assert.equal(rows[0].children[2].textContent,'增加用户额度 · alice（ID 2）、bob（ID 3）、carol（ID 4）等 8 位用户');
  for(const [i,[,label]] of rules.entries())assert.equal(rows[i+1].children[2].textContent,label+' · 规则 ID 9（用户组 team-a、team-b）');
  await rows[1].lastChild.firstChild.fire('click');const item=h.elements.get('records-items').firstChild;assert.equal(item.children[0].textContent,'定时规则 / ID 9');assert.equal(item.children[1].textContent,'规则 ID 9（用户组 team-a、team-b）');
});
