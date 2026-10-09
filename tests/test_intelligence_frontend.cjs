const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');

test('one charged request, sandbox POST preview/replay, safe source text and no refresh history',async()=>{
  const root=path.join(__dirname,'../src/new_api_cockpit');
  const html=fs.readFileSync(path.join(root,'templates/intelligence.html'),'utf8');
  const script=fs.readFileSync(path.join(root,'static/intelligence.js'),'utf8');
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='';this.textContent='';this.disabled=false;this.style={};this.contentWindow={};this.clientWidth=1280;this.clientHeight=720;}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
    setAttribute(key,value){this[key]=value;}
    addEventListener(name,fn){this.events[name]=fn;}
    fire(name){return this.events[name]?.({preventDefault(){}});}
    remove(){this.removed=true;}
    submit(){forms.push({target:this.target,action:this.action,method:this.method,fields:Object.fromEntries(this.children.map(n=>[n.name,n.value]))});}
  }
  const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const forms=[],calls=[],pending=[],events={};let unique=0,authChecks=0;
  const context={document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),body:new Element('body'),querySelector:()=>({content:'fixture-session'}),addEventListener:(name,fn)=>events[name]=fn},window:{addEventListener:(name,fn)=>events[name]=fn,CockpitAuth:{ensure:async()=>{authChecks++;}}},AbortSignal,AbortController,crypto:{randomUUID:()=>String(++unique)},console,fetch:(url,init)=>{calls.push({url,init});return new Promise(resolve=>pending.push(resolve));}};
  const settle=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
  vm.runInNewContext(script,context);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/cockpit/api/intelligence/models');
  pending[0]({ok:true,json:async()=>({rows:[{model:'model-a',group:'a'},{model:'model-b',group:'b'}]})});await settle();
  assert.equal(elements.get('intelligence-model').value,'model-a');assert.equal(elements.get('intelligence-group').value,'a');
  elements.get('intelligence-search').value='MODEL-B';elements.get('intelligence-search').fire('input');
  assert.equal(elements.get('intelligence-model').value,'model-b');assert.equal(elements.get('intelligence-group').value,'b');
  const first=elements.get('intelligence-test').fire('submit');
  assert.equal(calls.length,2);assert.equal(elements.get('intelligence-start').disabled,true);
  await elements.get('intelligence-test').fire('submit');assert.equal(calls.length,2);
  assert.deepEqual(JSON.parse(calls[1].init.body),{model:'model-b'});
  assert.equal(calls[1].init.method,'POST');assert.equal(calls[1].init.headers['X-Intelligence-Request'],'1');
  const output='<html><svg/><script>window.test=1</script></html>';
  pending[1]({ok:true,json:async()=>({model:'model-b',group:'actual',elapsed_ms:1234,html:output,source:output,warning:''})});await first;
  assert.equal(elements.get('intelligence-source').textContent,output);
  assert.equal(elements.get('intelligence-source').innerHTML,undefined);
  assert.equal(elements.get('intelligence-result-info').textContent,'model-b · actual · 1.23 秒');
  let frame=elements.get('intelligence-preview').children[0];
  assert.equal(frame.tag,'iframe');assert.equal(frame.sandbox,'allow-scripts');assert.equal(frame.srcdoc,undefined);
  assert.equal(frame.style.transform,'scale(0.8)');
  events.message({source:{},data:{type:'cockpit-intelligence-size',width:1600,height:1200}});
  assert.equal(frame.style.height,'900px');
  events.message({source:frame.contentWindow,data:{type:'cockpit-intelligence-size',width:1600,height:1200}});
  assert.equal(frame.style.height,'1200px');assert.equal(frame.style.transform,'scale(0.6)');assert.equal(frame.style.top,'0px');
  for(const height of [NaN,Infinity,0,-1,16385,'1200'])events.message({source:frame.contentWindow,data:{type:'cockpit-intelligence-size',width:1600,height}});
  assert.equal(frame.style.height,'1200px');
  assert.equal(forms[0].method,'POST');assert.equal(forms[0].action,'/cockpit/api/intelligence/preview');
  assert.equal(forms[0].fields.html,output);assert.equal(forms[0].fields.session_id,'fixture-session');
  assert.equal(authChecks,1);
  await elements.get('intelligence-replay').fire('click');
  assert.equal(calls.length,2);assert.equal(forms.length,2);
  assert.equal(authChecks,2);
  assert.notEqual(forms[0].target,forms[1].target);
  const replayFrame=elements.get('intelligence-preview').children[0];
  events.message({source:frame.contentWindow,data:{type:'cockpit-intelligence-size',width:1600,height:1200}});
  assert.equal(replayFrame.style.height,'900px');
  context.window.CockpitAuth.ensure=async()=>{throw new Error('登录账号已切换');};
  await elements.get('intelligence-replay').fire('click');assert.equal(forms.length,2);
  assert.equal(elements.get('intelligence-status').textContent,'登录账号已切换');
  context.window.CockpitAuth.ensure=async()=>{authChecks++;};
  elements.get('intelligence-source-button').fire('click');assert.equal(elements.get('intelligence-preview').hidden,true);
  events.pagehide();assert.equal(elements.get('intelligence-result').hidden,true);assert.equal(elements.get('intelligence-source').textContent,'');
  events.pageshow({persisted:true});assert.equal(elements.get('intelligence-preview').children.length,0);
  // A result delivered after navigating away cannot repopulate the old page.
  const abandoned=elements.get('intelligence-test').fire('submit');assert.equal(calls.length,3);
  events.pagehide();assert.equal(calls[2].init.signal.aborted,true);events.pageshow({persisted:true});
  pending[2]({ok:true,json:async()=>({model:'model-b',group:'b',html:output,source:output})});await abandoned;
  assert.equal(elements.get('intelligence-result').hidden,true);assert.equal(forms.length,2);
  const failed=elements.get('intelligence-test').fire('submit');
  pending[3]({ok:false,json:async()=>({error:'已超时，可能计费；未自动重试。'})});await failed;
  assert.equal(calls.length,4);assert.equal(elements.get('intelligence-result').hidden,true);
  assert.equal(elements.get('intelligence-status').textContent,'已超时，可能计费；未自动重试。');
  const incomplete=elements.get('intelligence-test').fire('submit');
  pending[4]({ok:true,json:async()=>({model:'model-b',group:'b',elapsed_ms:10,html:null,source:'partial source',warning:'输出截断'})});await incomplete;
  assert.equal(elements.get('intelligence-source').hidden,false);assert.equal(elements.get('intelligence-source').textContent,'partial source');
  assert.equal(elements.get('intelligence-effect').disabled,true);assert.equal(elements.get('intelligence-replay').disabled,true);assert.equal(forms.length,2);
  for(const forbidden of ['localStorage','sessionStorage','indexedDB','/runs','setInterval','innerHTML'])assert.equal(script.includes(forbidden),false);
  assert.equal(html.includes('history'),false);
});

test('sandbox dimensions include content clipped above its viewport; no result data leaves it',()=>{
  const script=fs.readFileSync(path.join(__dirname,'../src/new_api_cockpit/static/intelligence-preview.js'),'utf8');
  const messages=[];let resize;
  const context={document:{readyState:'complete',body:{scrollWidth:1280,scrollHeight:900},documentElement:{scrollWidth:1280,scrollHeight:900},querySelectorAll:()=>[
    {tagName:'H1',getBoundingClientRect:()=>({top:-50,bottom:0,left:0,right:1280})},
    {tagName:'SVG',getBoundingClientRect:()=>({top:0,bottom:950,left:0,right:1280})},
  ]},window:{addEventListener(){}},parent:{postMessage:(message,origin)=>messages.push({message,origin})},requestAnimationFrame:fn=>fn(),ResizeObserver:class{constructor(fn){resize=fn;}observe(){}}};
  vm.runInNewContext(script,context);
  assert.equal(messages.length,1);assert.deepEqual(JSON.parse(JSON.stringify(messages[0])),{message:{type:'cockpit-intelligence-size',width:1280,height:1000},origin:'*'});
  resize();assert.equal(messages.length,1);
  for(const field of ['html','source','key','token','cookie'])assert.equal(field in messages[0].message,false);
});
