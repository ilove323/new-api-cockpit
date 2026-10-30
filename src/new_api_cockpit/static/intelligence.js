/* One charged streaming request. No retry, background job or result storage. */
(function(){
  'use strict';
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);if(!$('intelligence-test'))return;
  let models=[],result=null,busy=false,frame=null,ticket=0,controller=null;
  let clock=null,started=0,stage='checking',received=0;
  // Only names this page's preview frames, never an auth or model request ID.
  let previewSequence=0;
  let previewWidth=1280,previewHeight=900,previewGrowths=0;
  function fit(){
    if(!frame||frame.tagName==='IMG')return;
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
  function stopClock(){if(clock!==null)clearTimeout(clock);clock=null;}
  function progress(next){
    if(next)stage=next;
    const labels={checking:'正在校验模型',key:'正在准备测试 KEY',waiting:'等待模型响应',thinking:'模型正在思考',generating:'正在生成 SVG'};
    status((labels[stage]||labels.waiting)+(received?' · '+received+' 字符':'')+' · 已用 '+Math.floor((Date.now()-started)/1000)+' 秒');
  }
  function startClock(){
    started=Date.now();stage='checking';received=0;
    const current=ticket;
    function tick(){if(current!==ticket||!busy)return;progress();clock=setTimeout(tick,1000);}
    tick();
  }
  function clear(){
    stopClock();
    ticket++;controller?.abort();controller=null;result=null;frame=null;$('intelligence-preview').replaceChildren();$('intelligence-source').textContent='';
    $('intelligence-result').hidden=true;$('intelligence-result-info').textContent='';
    $('intelligence-test').setAttribute('aria-busy','false');
  }

  async function consume(response,onEvent){
    if(!(response.headers?.get('Content-Type')||'').toLowerCase().includes('text/event-stream')){
      let data;try{data=await response.json();}catch{throw new Error('生成请求失败（HTTP '+response.status+'）。');}
      if(!response.ok)throw new Error(data.error||'测试失败。');return data;
    }
    if(!response.ok||!response.body)throw new Error('生成连接不可用。');
    const reader=response.body.getReader(),decoder=new TextDecoder('utf-8',{fatal:true});
    let line='',data=[],type='',skipLF=false,terminal=false,output=null,size=0;
    function endLine(){
      if(!line){
        if(data.length){
          let event;try{event=JSON.parse(data.join('\n'));}catch{throw new Error('生成状态格式无效。');}
          if(!event||event.type!==type)throw new Error('生成状态格式无效。');
          if(type==='error')throw new Error(event.error||'模型生成失败。');
          if(type==='result'){output=event.result;terminal=true;}
          else if(type==='status'||type==='text'||type==='heartbeat')onEvent(event);
        }
        data=[];type='';size=0;return;
      }
      const split=line.indexOf(':'),field=split<0?line:line.slice(0,split);
      let value=split<0?'':line.slice(split+1);if(value.startsWith(' '))value=value.slice(1);
      if(field==='data')data.push(value);if(field==='event')type=value;line='';
    }
    try{
      while(!terminal){
        const chunk=await reader.read();
        const text=decoder.decode(chunk.value||new Uint8Array(),{stream:!chunk.done});
        for(const char of text){
          if(char==='\n'&&skipLF){skipLF=false;continue;}skipLF=char==='\r';
          if(++size>8*1024*1024)throw new Error('生成状态超过大小限制。');
          if(char==='\r'||char==='\n')endLine();else line+=char;
          if(terminal)break;
        }
        if(chunk.done)break;
      }
      if(!terminal||!output)throw new Error('生成连接中断，收到的源码已保留。');
      return output;
    }finally{
      try{await reader.cancel();}catch{}reader.releaseLock();
    }
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
    const current=ticket,html=result?.html,svg=result?.svg;if(!html)return;
    // Generation may outlast the access token. Refresh before the form POST,
    // without replaying inference or accepting a different login account.
    await window.CockpitAuth.ensure();if(current!==ticket||result?.html!==html)return;
    if(svg){
      // Image mode cannot execute SVG scripts or load external resources. Keep
      // model markup out of the parent DOM, URLs sent to the server and files.
      const image=document.createElement('img');image.alt='鹈鹕骑车测试生成的 SVG';image.referrerPolicy='no-referrer';
      frame=image;$('intelligence-preview').replaceChildren(image);
      image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);
      try{await image.decode();}catch{if(current===ticket)throw new Error('SVG 预览加载失败，源码已保留。');return;}
      if(current===ticket&&frame===image)tab(false);return;
    }
    frame=document.createElement('iframe');frame.name='cockpit-pelican-'+(++previewSequence);frame.title='鹈鹕骑车测试生成的 SVG';
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
      models=data.rows||[];options();if(!models.length)status('当前用户没有可测试的文本模型。');
    }catch(error){status(error.message,true);$('intelligence-model').replaceChildren();$('intelligence-model').disabled=true;}
  }
  $('intelligence-search').addEventListener('input',options);$('intelligence-model').addEventListener('change',select);
  $('intelligence-test').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!$('intelligence-model').value)return;
    const model=$('intelligence-model').value;busy=true;clear();select();$('intelligence-model').disabled=true;$('intelligence-search').disabled=true;
    const current=ticket;controller=new AbortController();
    $('intelligence-test').setAttribute('aria-busy','true');
    for(const id of ['intelligence-effect','intelligence-replay','intelligence-fullscreen'])$(id).disabled=true;
    startClock();
    try{
      // The backend allows 300 s for generation; leave room for KEY preparation
      // and delivering the final event. This does not extend model generation.
      const response=await fetch('/cockpit/api/intelligence/test',{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream','X-Intelligence-Request':'1'},body:JSON.stringify({model}),signal:AbortSignal.any([controller.signal,AbortSignal.timeout(420000)])});
      const data=await consume(response,event=>{
        if(current!==ticket)return;
        if(event.type==='status')progress(event.stage);
        if(event.type==='text'&&typeof event.text==='string'){
          received+=Array.from(event.text).length;
          $('intelligence-source').textContent+=event.text;$('intelligence-result').hidden=false;
          $('intelligence-result-info').textContent=model+' · 生成中';tab(true);progress('generating');
        }
      });
      if(current!==ticket)return;stopClock();
      result=data;$('intelligence-source').textContent=data.source||'';$('intelligence-result').hidden=false;
      $('intelligence-result-info').textContent=data.model+' · '+data.group+' · '+(data.elapsed_ms/1000).toFixed(2)+' 秒';
      $('intelligence-effect').disabled=!data.html;$('intelligence-replay').disabled=!data.html;$('intelligence-fullscreen').disabled=!data.html;
      if(data.html)await preview();else tab(true);if(current===ticket)status(data.warning||'生成完成。',Boolean(data.warning));
    }catch(error){if(current===ticket){
      $('intelligence-result-info').textContent=received?model+' · 未完成':'';
      status(error.name==='TimeoutError'?'等待超时，收到的源码已保留。':error.message,true);
    }}
    finally{if(current===ticket){stopClock();busy=false;controller=null;$('intelligence-test').setAttribute('aria-busy','false');$('intelligence-search').disabled=false;options();}}
  });
  $('intelligence-effect').addEventListener('click',()=>tab(false));$('intelligence-source-button').addEventListener('click',()=>tab(true));
  $('intelligence-replay').addEventListener('click',()=>preview().catch(error=>status(error.message,true)));
  $('intelligence-fullscreen').addEventListener('click',async()=>{try{if(frame)await $('intelligence-preview').requestFullscreen();}catch{status('浏览器不允许全屏，请在当前页面查看。',true);}});
  // Back/forward cache must not become an accidental result history.
  window.addEventListener('pagehide',()=>{clear();busy=false;$('intelligence-search').disabled=false;options();});
  window.addEventListener('pageshow',event=>{if(event.persisted){clear();status();options();}});
  load();
})();
