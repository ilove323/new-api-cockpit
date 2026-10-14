/* Logged channel quality. No writes, timers, automatic retries or routing actions. */
(function(){
  'use strict';
  const numeric=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2});
  const currency=new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const percentage=new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const money=value=>value==null?'—':currency.format(Number(value));
  const rate=value=>value==null?'无数据':numeric.format(value)+'%';
  const healthRate=value=>value==null||!Number.isFinite(Number(value))?'无数据':percentage.format(value)+'%';
  const successTone=value=>value==null||!Number.isFinite(Number(value))?'':value>=90?'quality-good':value>=70?'quality-warn':'quality-bad';
  const tokensPerSecond=value=>value==null||!Number.isFinite(Number(value))?'无数据':numeric.format(value);
  const throughput=value=>value==null||!Number.isFinite(Number(value))?'无数据':tokensPerSecond(value)+' t/s';
  function latency(value){
    if(value==null||!Number.isFinite(Number(value)))return '无数据';
    return Number(value)>=1000?numeric.format(Number(value)/1000)+'s':numeric.format(value)+'ms';
  }
  const latencyPair=(row,percentile)=>latency(row['frt_'+percentile+'_ms'])+' / '+latency(row['duration_'+percentile+'_ms']);
  const names={model:'模型',channel_id:'渠道'};
  function filterText(name,selected,choices){
    const values=[...selected];
    return !values.length?'全部'+name:values.length===1?(choices.get(values[0])||values[0]||'未指定'):'已选 '+values.length+' 项';
  }
  function queryParams(values,selected={}){
    // Fixed page defaults: channel rows, all request types, confirmed direct speed samples.
    const params=new URLSearchParams({start:values.start,end:values.end});
    for(const key of Object.keys(names))for(const value of selected[key]||[])params.append(key,value);
    return params;
  }
  function logsURL(row,snapshot){
    // New API logs require a concrete channel ID.
    if(!(row.channel_id>0)||!snapshot||!row.model_name)return null;
    const query=new URLSearchParams({type:'0',page:'1',model:row.model_name,channel:String(row.channel_id),
      startTime:String(snapshot.start_ts*1000),endTime:String((snapshot.end_ts-1)*1000)});
    return '/usage-logs/common?'+query;
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={money,rate,healthRate,successTone,tokensPerSecond,throughput,latency,latencyPair,logsURL,queryParams,filterText};
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);if(!$('quality-query'))return;
  let optionWindow='',requestedPeriod=null,snapshot=null,controller=null,ticket=0,page=0;
  const selected={},menus={};
  const textCell=(tr,value)=>{const td=document.createElement('td');td.textContent=value==null?'—':String(value);tr.append(td);return td;};
  const note=(td,value)=>{const span=document.createElement('span');span.className='quality-note';span.textContent=value;td.append(span);};
  function updateSummary(key,choices){
    const summary=menus[key].summary;
    summary.textContent=filterText(names[key],selected[key],choices);
    summary.title=[...selected[key]].map(value=>choices.get(value)||value||'未指定').join('、');
  }
  function updateMenu(key,options){
    const {body,search}=menus[key];
    const choices=new Map(options.map(option=>[String(option.value),option.label]));
    for(const value of selected[key])if(!choices.has(value))choices.set(value,value||'未指定');
    const choiceKey=JSON.stringify([...choices]);
    // Keep open menus, checkbox focus and scroll position when only the result changes.
    if(menus[key].choiceKey===choiceKey){
      for(const input of body.querySelectorAll('input[type="checkbox"]'))input.checked=selected[key].has(input.value);
      updateSummary(key,choices);return;
    }
    menus[key].choiceKey=choiceKey;body.replaceChildren(search);
    for(const [value,label] of choices){
      const item=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=value;input.checked=selected[key].has(value);
      input.addEventListener('change',()=>{if(input.checked)selected[key].add(value);else selected[key].delete(value);updateSummary(key,choices);query(requestedPeriod);});
      item.append(input,document.createTextNode(label));item.dataset.search=label;item.hidden=!label.includes(search.value);body.append(item);
    }
    updateSummary(key,choices);
  }
  for(const key of Object.keys(names)){
    selected[key]=new Set();const picker=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('div'),search=document.createElement('input');
    picker.className='filter-picker';summary.textContent='全部'+names[key];body.className='filter-menu';search.type='search';search.className='filter-search';search.autocomplete='off';search.placeholder='输入关键词筛选选项';search.setAttribute('aria-label','筛选'+names[key]);
    search.addEventListener('input',()=>{for(const label of body.querySelectorAll('label'))label.hidden=!label.dataset.search.includes(search.value);});
    body.append(search);picker.append(summary,body);$('quality-filters').append(picker);menus[key]={body,summary,search};
  }
  function populate(data){
    const options=data.options;let selectionChanged=false;
    for(const key of Object.keys(names)){
      const incoming=key==='model'?(options.models||[]).filter(v=>typeof v==='string').map(value=>({value,label:value||'未指定'})):
        (options.channels||[]).filter(r=>r.channel_status===1&&!r.is_deleted).map(r=>({value:String(r.channel_id),label:(r.channel_name||'未知渠道')+' (ID '+r.channel_id+')'}));
      // Model options survive refinement. Channel options always follow the live catalog.
      if(key==='channel_id'){
        const allowed=new Set(incoming.map(v=>v.value));
        for(const value of selected[key])if(!allowed.has(value)){selected[key].delete(value);selectionChanged=true;}
        menus[key].options=incoming;
      }else menus[key].options=[...new Map([...(menus[key].options||[]),...incoming].map(v=>[v.value,v])).values()];
      updateMenu(key,key==='model'?menus[key].options.sort((a,b)=>a.label.localeCompare(b.label)):menus[key].options);
    }
    return selectionChanged;
  }
  function showMetrics(){
    const t=snapshot.totals,container=$('quality-metrics');container.replaceChildren();
    for(const [label,value] of [['请求次数',numeric.format(t.request_count)],['失败调用',numeric.format(t.failure_count)],['日志费用（元）',money(t.amount)]]){
      const card=document.createElement('div'),title=document.createElement('span'),strong=document.createElement('strong');card.className='quality-metric';title.textContent=label;strong.textContent=value;card.append(title,strong);container.append(card);
    }
  }
  function showHealth(){
    const t=snapshot.totals,success=$('quality-health-success');success.textContent=healthRate(t.success_rate);success.className='quality-health-value '+successTone(t.success_rate);
    $('quality-health-latency').textContent=latency(t.avg_duration_ms);
    $('quality-health-throughput').textContent=throughput(t.avg_tokens_per_second);
    const models=$('quality-top-models');models.replaceChildren();
    for(const model of snapshot.top_models||[]){
      const item=document.createElement('div'),name=document.createElement('span'),result=document.createElement('span'),dot=document.createElement('span'),value=document.createElement('strong');
      item.className='quality-top-model';name.className='quality-top-model-name';name.textContent=model.model_name||'未指定';name.title=name.textContent;
      result.className='quality-model-rate '+successTone(model.success_rate);dot.className='quality-rate-dot';dot.setAttribute('aria-hidden','true');value.textContent=healthRate(model.success_rate);
      result.append(dot,value);item.append(name,result);models.append(item);
    }
    if(!(snapshot.top_models||[]).length)models.textContent='暂无请求数据';
    $('quality-health').setAttribute('aria-busy','false');
  }
  function setStatus(message='',error=false){
    const status=$('quality-status');status.textContent=message;status.hidden=!message;status.className='quality-status'+(error?' error':'');
  }
  function addLogLink(container,row){
    const url=logsURL(row,snapshot);if(!url)return;
    const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='查看日志';
    link.title='按此模型、渠道和统计时间查看日志';container.append(link);
  }
  function render(){
    const body=$('quality-rows');body.replaceChildren();
    for(const row of snapshot.rows.slice(page*50,(page+1)*50)){
      const tr=document.createElement('tr');textCell(tr,row.model_name);
      textCell(tr,row.channel_name+' (ID '+row.channel_id+')');
      textCell(tr,numeric.format(row.request_count));
      const success=textCell(tr,rate(row.success_rate));success.className=successTone(row.success_rate);
      const failures=textCell(tr,row.failure_count);for(const [code,n] of Object.entries(row.failure_codes))note(failures,code+' '+n+'次');
      for(const percentile of ['p50','p95']){
        const cell=textCell(tr,latencyPair(row,percentile));cell.title='首字 '+percentile.toUpperCase()+' / 总耗时 '+percentile.toUpperCase();
      }
      const speed=textCell(tr,tokensPerSecond(row.avg_tokens_per_second));speed.title='成功请求输出 Token 合计 ÷ 有效渠道调用耗时（秒），包含首字等待，不是纯生成速度';
      textCell(tr,money(row.amount));const actions=textCell(tr,'');addLogLink(actions,row);body.append(tr);
    }
    if(!snapshot.rows.length){const tr=document.createElement('tr'),td=textCell(tr,'当前时间和筛选范围没有已记录数据');td.colSpan=10;body.append(tr);}
    $('quality-page').textContent='共 '+snapshot.rows.length+' 行 · 第 '+(page+1)+' / '+Math.max(1,Math.ceil(snapshot.rows.length/50))+' 页';
    $('quality-prev').disabled=page===0;$('quality-next').disabled=(page+1)*50>=snapshot.rows.length;
  }
  function invalidate(){
    ticket++;controller?.abort();snapshot=null;
    $('quality-rows').replaceChildren();$('quality-metrics').replaceChildren();$('quality-top-models').replaceChildren();
    for(const id of ['quality-health-success','quality-health-latency','quality-health-throughput'])$(id).textContent='—';
    $('quality-health-success').className='quality-health-value';$('quality-health').setAttribute('aria-busy','true');
    $('quality-prev').disabled=true;$('quality-next').disabled=true;$('quality-page').textContent='';
  }
  async function query(period){
    const params=queryParams(period||{start:$('quality-start').value,end:$('quality-end').value},selected);
    requestedPeriod={start:params.get('start'),end:params.get('end')};
    const windowKey=[requestedPeriod.start,requestedPeriod.end].join('\0');if(windowKey!==optionWindow){optionWindow=windowKey;for(const menu of Object.values(menus))menu.options=[];}invalidate();const current=ticket;controller=new AbortController();
    $('quality-submit').disabled=true;setStatus('正在查询…');
    try{
      const response=await fetch('/cockpit/api/quality/summary?'+params,{signal:controller.signal});const data=await response.json();
      if(current!==ticket)return;if(!response.ok)throw new Error(data.error||'渠道质量查询失败');
      if(populate(data)){query(requestedPeriod);return;}
      snapshot=data;page=0;showMetrics();showHealth();render();setStatus();
    }catch(error){if(current===ticket&&error.name!=='AbortError'){setStatus(error.message,true);$('quality-health').setAttribute('aria-busy','false');}}
    finally{if(current===ticket)$('quality-submit').disabled=false;}
  }
  $('quality-query').addEventListener('submit',event=>{event.preventDefault();query();});
  function beijing(ms){return new Date(ms+8*3600000).toISOString().slice(0,19);}
  for(const button of document.querySelectorAll('[data-quality-days]'))button.addEventListener('click',()=>{const now=Date.now();$('quality-start').value=beijing(now-Number(button.dataset.qualityDays)*86400000);$('quality-end').value=beijing(now);query();});
  $('quality-prev').addEventListener('click',()=>{if(snapshot&&page>0){page--;render();}});$('quality-next').addEventListener('click',()=>{if(snapshot&&(page+1)*50<snapshot.rows.length){page++;render();}});
  query();
})();
