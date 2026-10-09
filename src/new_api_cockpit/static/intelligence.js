/* One synchronous, charged request. No background job, history or result storage. */
(function(){
  'use strict';
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);if(!$('intelligence-test'))return;
  let models=[],result=null,busy=false,frame=null,ticket=0,controller=null;
  let previewWidth=1280,previewHeight=900,previewGrowths=0;
  function fit(){
    if(!frame)return;
    const box=$('intelligence-preview'),width=box.clientWidth,height=box.clientHeight;
    if(!width||!height)return;
    const scale=Math.min(width/previewWidth,height/previewHeight);
    frame.style.width=previewWidth+'px';frame.style.height=previewHeight+'px';frame.style.transform='scale('+scale+')';
    frame.style.left=(width-previewWidth*scale)/2+'px';frame.style.top=(height-previewHeight*scale)/2+'px';
  }
  if(typeof ResizeObserver==='function')new ResizeObserver(fit).observe($('intelligence-preview'));
  window.addEventListener('resize',fit);document.addEventListener('fullscreenchange',fit);
  window.addEventListener('message',event=>{
    const data=event.data;
    if(!frame||event.source!==frame.contentWindow||!data||data.type!=='cockpit-intelligence-size')return;
    if(![data.width,data.height].every(v=>typeof v==='number'&&Number.isFinite(v)&&v>0&&v<=16384))return;
    // Only dimensions from the current sandbox, never markup or commands.
    // Bound feedback from viewport-dependent CSS; ignore subpixel rounding.
    if(previewGrowths>=8||(data.width<=previewWidth+2&&data.height<=previewHeight+2))return;
    previewGrowths++;
    previewWidth=Math.max(previewWidth,Math.ceil(data.width));previewHeight=Math.max(previewHeight,Math.ceil(data.height));fit();
  });
  const status=(message='',error=false)=>{const node=$('intelligence-status');node.textContent=message;node.hidden=!message;node.className=error?'error':'';};
  function clear(){
    ticket++;controller?.abort();controller=null;result=null;frame=null;$('intelligence-preview').replaceChildren();$('intelligence-source').textContent='';
    $('intelligence-result').hidden=true;$('intelligence-result-info').textContent='';
  }
  function select(){
    const choice=models.find(row=>row.model===$('intelligence-model').value);
    $('intelligence-group').value=choice?.group||'—';$('intelligence-start').disabled=busy||!choice;
  }
  function options(){
    const previous=$('intelligence-model').value,search=$('intelligence-search').value.toLowerCase();
    const choices=models.filter(row=>row.model.toLowerCase().includes(search));
    const items=choices.map(row=>{const option=document.createElement('option');option.value=row.model;option.textContent=row.model;return option;});
    if(!items.length){const empty=document.createElement('option');empty.value='';empty.textContent='没有可测试的模型';items.push(empty);}
    $('intelligence-model').replaceChildren(...items);
    $('intelligence-model').value=choices.some(row=>row.model===previous)?previous:choices[0]?.model||'';
    $('intelligence-model').disabled=busy||!choices.length;select();
  }
  function tab(source){
    $('intelligence-preview').hidden=source;$('intelligence-source').hidden=!source;
    $('intelligence-effect').setAttribute('aria-pressed',String(!source));$('intelligence-source-button').setAttribute('aria-pressed',String(source));
    if(!source)fit();
  }
  async function preview(){
    const current=ticket,html=result?.html;if(!html)return;
    // Generation may outlast the access token. Refresh before the form POST,
    // without replaying inference or accepting a different login account.
    await window.CockpitAuth.ensure();if(current!==ticket||result?.html!==html)return;
    frame=document.createElement('iframe');frame.name='cockpit-pelican-'+crypto.randomUUID();frame.title='鹈鹕骑车测试生成的动画';
    frame.setAttribute('sandbox','allow-scripts');frame.referrerPolicy='no-referrer';
    previewWidth=1280;previewHeight=900;previewGrowths=0;
    $('intelligence-preview').replaceChildren(frame);
    // POST the current in-memory document into the frame. It has a separate
    // HTTP CSP, including sandbox, even when opened outside this iframe.
    const form=document.createElement('form');form.method='POST';form.action='/cockpit/api/intelligence/preview';form.target=frame.name;form.hidden=true;
    const fields={html,session_id:document.querySelector('meta[name="cockpit-session"]')?.content||''};
    for(const [name,value] of Object.entries(fields)){const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;form.append(input);}
    document.body.append(form);form.submit();form.remove();tab(false);
  }
  async function load(){
    try{
      const response=await fetch('/cockpit/api/intelligence/models',{signal:AbortSignal.timeout(20000)}),data=await response.json();
      if(!response.ok)throw new Error(data.error||'模型列表读取失败');
      models=data.rows||[];options();if(!models.length)status('当前管理员没有可测试的文本模型。');
    }catch(error){status(error.message,true);$('intelligence-model').replaceChildren();$('intelligence-model').disabled=true;}
  }
  $('intelligence-search').addEventListener('input',options);$('intelligence-model').addEventListener('change',select);
  $('intelligence-test').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!$('intelligence-model').value)return;
    const model=$('intelligence-model').value;busy=true;clear();select();$('intelligence-model').disabled=true;$('intelligence-search').disabled=true;
    const current=ticket;controller=new AbortController();
    status('正在等待模型生成，请勿刷新或重复提交…');
    try{
      const response=await fetch('/cockpit/api/intelligence/test',{method:'POST',headers:{'Content-Type':'application/json','X-Intelligence-Request':'1'},body:JSON.stringify({model}),signal:AbortSignal.any([controller.signal,AbortSignal.timeout(300000)])});
      const data=await response.json();if(current!==ticket)return;if(!response.ok)throw new Error(data.error||'测试失败；未自动重试。');
      result=data;$('intelligence-source').textContent=data.source||'';$('intelligence-result').hidden=false;
      $('intelligence-result-info').textContent=data.model+' · '+data.group+' · '+(data.elapsed_ms/1000).toFixed(2)+' 秒';
      $('intelligence-effect').disabled=!data.html;$('intelligence-replay').disabled=!data.html;$('intelligence-fullscreen').disabled=!data.html;
      if(data.html)await preview();else tab(true);if(current===ticket)status(data.warning||'生成完成。',Boolean(data.warning));
    }catch(error){if(current===ticket)status(error.name==='TimeoutError'?'等待超时，模型请求可能仍在执行或已产生费用；未自动重试。':error.message,true);}
    finally{if(current===ticket){busy=false;controller=null;$('intelligence-search').disabled=false;options();}}
  });
  $('intelligence-effect').addEventListener('click',()=>tab(false));$('intelligence-source-button').addEventListener('click',()=>tab(true));
  $('intelligence-replay').addEventListener('click',()=>preview().catch(error=>status(error.message,true)));
  $('intelligence-fullscreen').addEventListener('click',async()=>{try{if(frame)await $('intelligence-preview').requestFullscreen();}catch{status('浏览器不允许全屏，请在当前页面查看。',true);}});
  // Back/forward cache must not become an accidental result history.
  window.addEventListener('pagehide',()=>{clear();busy=false;$('intelligence-search').disabled=false;options();});
  window.addEventListener('pageshow',event=>{if(event.persisted){clear();status();options();}});
  load();
})();
