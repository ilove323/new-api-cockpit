/* DOM fixtures only, no browser and no real scheduled quota changes. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../src/new_api_statistics');
const html=fs.readFileSync(path.join(root,'templates/quota.html'),'utf8');
class Element{
  constructor(){this.children=[];this.events={};this.attrs={};this.value='';this.checked=false;this.textContent='';this.hidden=false;this.disabled=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(key,fn){(this.events[key]??=[]).push(fn);}
  async fire(key){for(const fn of this.events[key]||[])await fn({preventDefault(){}});}
  querySelectorAll(){return this.children.flatMap(label=>label.children).filter(input=>input.checked);}
  checkValidity(){return true;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  scrollIntoView(){}
  focus(){}
}
const fixture={configured:true,current_user_id:1,current_username:'admin',groups:['team-a','<img src=x>'],rows:[]};
const rule={id:1,enabled:true,period:'monthly',operation:'add',amount_yuan:'100.01',executor_username:'admin',version:2,next_run_at:'2026-11-01T00:00:00+08:00',groups:['team-a'],can_edit:true};
function harness(data=fixture){
  const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const calls=[],confirmations=[];
  const ctx={document:{getElementById:id=>elements.get(id),createElement:()=>new Element(),createTextNode:text=>({textContent:text})},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>data};},
    confirm:message=>{confirmations.push(message);return true;}};
  ctx.window=ctx;vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(root,'static/quota-schedule.js'),'utf8'),ctx);
  return {ctx,elements,calls,confirmations,open:()=>elements.get('quota-settings').fire('click')};
}
test('settings opens rule tab, safely renders group choices and never requests mutations on load',async()=>{
  const h=harness();await h.open();await h.elements.get('schedule-new').fire('click');
  assert.equal(h.elements.get('quota-settings-dialog').open,true);
  assert.equal(h.elements.get('schedule-rules-tab').attrs['aria-selected'],'true');
  assert.equal(h.elements.get('schedule-form').hidden,false);
  assert.ok(h.elements.get('schedule-groups').children.some(label=>label.children[1].textContent==='<img src=x>'));
  assert.ok(h.calls.every(call=>call.options.method==='GET'));
  assert.equal(h.elements.get('schedule-enabled').checked,false);
});
test('unconfigured monitor leaves manual quota page alone and disables creation',async()=>{
  const h=harness({configured:false,rows:[],groups:[]});await h.open();
  assert.equal(h.elements.get('schedule-new').disabled,true);
  assert.ok(h.elements.get('schedule-status').textContent.includes('未配置'));
  assert.equal(h.calls.length,1);
});
test('save submits exact amount and group names once, with JSON and dedicated action header',async()=>{
  const h=harness({...fixture,rows:[rule]});await h.open();await h.elements.get('schedule-new').fire('click');
  h.elements.get('schedule-groups').children.find(label=>label.children[0].value==='team-a').children[0].checked=true;
  h.elements.get('schedule-period').value='weekly';h.elements.get('schedule-operation').value='subtract';
  h.elements.get('schedule-amount').value='100.01';h.elements.get('schedule-enabled').checked=true;
  let resolve;const original=h.ctx.fetch;
  h.ctx.fetch=async(url,options)=>{if(options.method==='POST'){h.calls.push({url,options});return {ok:true,json:()=>new Promise(done=>{resolve=done;})};}return original(url,options);};
  const pending=h.elements.get('schedule-form').fire('submit');
  await h.elements.get('schedule-form').fire('submit');
  assert.equal(h.calls.filter(call=>call.options.method==='POST').length,1);
  const request=h.calls.find(call=>call.options.method==='POST');
  assert.deepEqual(JSON.parse(request.options.body),{groups:['team-a'],period:'weekly',operation:'subtract',amount_yuan:'100.01',enabled:true});
  assert.equal(request.options.headers['X-Quota-Action'],'schedule');
  assert.ok(h.confirmations[0].includes('已有启用规则'));
  assert.equal(h.elements.get('schedule-close').disabled,true);
  resolve({id:2});await pending;
  assert.equal(h.elements.get('schedule-form').hidden,true);
});
test('ambiguous rule creation cannot be resent until successful refresh',async()=>{
  const h=harness();await h.open();await h.elements.get('schedule-new').fire('click');
  h.elements.get('schedule-groups').children[0].children[0].checked=true;
  h.elements.get('schedule-amount').value='100';
  const original=h.ctx.fetch;let attempts=0;
  h.ctx.fetch=async(url,options)=>{if(options.method==='POST'){attempts++;throw new Error('network');}return original(url,options);};
  await h.elements.get('schedule-form').fire('submit');await h.elements.get('schedule-form').fire('submit');
  assert.equal(attempts,1);assert.equal(h.elements.get('schedule-save').disabled,true);
  await h.elements.get('schedule-refresh').fire('click');
  assert.equal(h.elements.get('schedule-save').disabled,false);
});
test('rules owned by other administrators have no enabled management controls',async()=>{
  const h=harness({...fixture,rows:[{...rule,can_edit:false}]});await h.open();
  const buttons=h.elements.get('schedule-rules').children[0].children[8].children;
  assert.ok(buttons.every(button=>button.disabled));
  assert.equal(h.elements.get('schedule-rules').children[0].children[4].textContent,'100.01');
});
test('execution records and user detail are paginated, two-decimal and read-only',async()=>{
  const h=harness();await h.open();
  const run={id:7,rule_id:1,scheduled_for:'2026-10-01T00:00:00+08:00',snapshot:{groups:['team-a'],operation:'add',amount_units:617283945},status:'unknown',success_count:4,failed_count:0,unknown_count:1,skipped_count:5,message:'请核对审计日志'};
  h.ctx.fetch=async(url,options)=>{h.calls.push({url,options});return {ok:true,json:async()=>url.includes('schedule-runs/7')?{run,rows:[{username:'alice',display_name:'Alice',group_name:'team-a',operation:'add',amount_yuan:'1234.56789',status:'unknown',message:'结果不明确'}],next_after:null}:{rows:[run],next_before:7}};};
  await h.elements.get('schedule-runs-tab').fire('click');
  const row=h.elements.get('schedule-runs').children[0];
  assert.equal(row.children[4].textContent,'1,234.57');assert.equal(row.children[5].textContent,'结果不明确');
  await row.children[7].children[0].fire('click');
  const item=h.elements.get('schedule-run-items').children[0];
  assert.equal(item.children[3].textContent,'1,234.57');assert.equal(item.children[0].children[0].textContent,'Alice');
  assert.ok(h.calls.every(call=>call.options.method==='GET'));
  await h.elements.get('schedule-run-close').fire('click');await h.elements.get('schedule-runs-more').fire('click');
  assert.ok(h.calls.some(call=>call.url.endsWith('?before=7')));
});
