/* Read once when selected; no KEY lookup, polling or per-user API requests. */
(function(root,create){
  'use strict';
  if(typeof module==='object'&&module.exports)module.exports=create;
  else{
    root.CockpitChannelFlow=create({document:root.document,request:(...args)=>root.fetch(...args)});
    root.addEventListener('pagehide',()=>root.CockpitChannelFlow.reset());
  }
})(typeof window==='object'?window:globalThis,function(options){
  'use strict';
  const {document,request}=options;
  let cache=null,pending=null,controller=null,version=0,visible=false,target=null,activeKey=null;
  const money=new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const count=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0});
  function node(tag,cls,text){
    const element=document.createElement(tag);element.className=cls;if(text!==undefined)element.textContent=text;return element;
  }
  function reset(){
    version++;controller?.abort();controller=null;pending=null;cache=null;visible=false;target=null;activeKey=null;
  }
  function hide(){visible=false;}
  function render(container,data){
    container.replaceChildren();container.setAttribute('aria-busy','false');
    container.append(node('p','channel-flow-hint','全部启用渠道 · 按本页已查询时段统计，不限账本及明细筛选'));
    if(!data.rows.length){container.append(node('p','channel-flow-empty','当前没有启用渠道'));return;}
    for(const channel of data.rows){
      const section=node('section','channel-flow-channel');section.dataset.channelId=String(channel.channel_id);
      const header=node('div','channel-flow-heading');
      const heading=node('h3','',`${channel.channel_name||'未命名渠道'}（#${channel.channel_id}）`);
      header.append(heading,node('span','channel-flow-total',`合计 ¥ ${money.format(Number(channel.amount))} · ${count.format(channel.user_count)} 位用户 · ${count.format(channel.request_count)} 次消费请求`));
      section.append(header);
      if(!channel.users.length)section.append(node('p','channel-flow-empty','所选时间内暂无消费'));
      for(const [index,user] of channel.users.entries()){
        const row=node('div','bar-row channel-flow-user');row.dataset.userId=String(user.user_id??'');
        const name=`${user.username}${user.user_id===null?'':`（#${user.user_id}）`}`;
        const label=node('span','bar-label',`${index+1}. ${name}`);label.title=name+(user.display_name?' · '+user.display_name:'');
        const track=node('div','bar-track'),bar=node('div','bar');
        bar.style.width=(user.percentage===null?0:Math.max(0,Math.min(100,Number(user.percentage))))+'%';track.append(bar);
        const value=node('span','bar-value channel-flow-value');
        value.append(node('span','',`¥ ${money.format(Number(user.amount))}`),node('span','channel-flow-percentage',user.percentage===null?'—':money.format(Number(user.percentage))+'%'));
        row.title=`${count.format(user.request_count)} 次消费请求`;row.append(label,track,value);section.append(row);
      }
      container.append(section);
    }
  }
  function show(container,range){
    const key=JSON.stringify([range.start,range.end]);visible=true;target=container;activeKey=key;
    if(cache?.key===key){render(container,cache.data);return Promise.resolve();}
    container.replaceChildren(node('p','channel-flow-empty','正在查询渠道分流…'));container.setAttribute('aria-busy','true');
    if(pending?.key===key)return pending.promise;
    controller?.abort();controller=new AbortController();
    const ticket=++version,signal=controller.signal;
    const job={key,promise:null};pending=job;
    job.promise=(async()=>{
      try{
        // Deliberately bypass Scope.request: this view covers ALL enabled channels.
        const response=await request('/cockpit/api/statistics/channel-flow?'+new URLSearchParams({start:range.start,end:range.end}),{signal});
        if(!response.ok){
          let message='渠道分流查询失败，请重试';try{message=(await response.json()).error||message;}catch{}
          throw new Error(message);
        }
        const data=await response.json();
        if(ticket!==version)return;
        if(data.start!==range.start||data.end!==range.end||!Array.isArray(data.rows))throw new Error('渠道分流响应与当前时间范围不一致，请重试');
        cache={key,data};if(visible&&activeKey===key)render(target,data);
      }catch(error){
        if(ticket!==version||signal.aborted)return;
        if(visible&&activeKey===key){
          const panel=target;panel.replaceChildren(node('p','error',error.message));panel.setAttribute('aria-busy','false');
          const retry=node('button','button-small','重新查询');retry.type='button';
          retry.addEventListener('click',()=>{cache=null;return show(panel,range);});panel.append(retry);
        }
      }finally{if(pending===job)pending=null;}
    })();
    return job.promise;
  }
  return {show,hide,reset};
});
