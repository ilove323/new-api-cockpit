/* Unified, read-only paginated audit view. No mutation or credential endpoint. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const states={running:'执行中',completed:'全部成功',partial:'部分完成',sending:'请求中（需核对）',success:'成功',failed:'失败',unknown:'结果不明确',conflict:'配置冲突'};
  const labels={'user.create':'新增用户','user.edit':'编辑用户','user.password':'重置密码','user.enable':'启用用户','user.disable':'禁用用户','user.delete':'删除用户','user.pat_create':'补建用户 PAT','token.create':'新增 KEY','token.edit':'编辑 KEY','token.quota':'KEY 额度调整','token.enable':'启用 KEY','token.disable':'禁用 KEY','token.delete':'删除 KEY','token.reveal':'查看 KEY','token.group':'KEY 改组','token.batch_group':'批量 KEY 改组','quota.add':'增加用户额度','quota.subtract':'减少用户额度','schedule.create':'新增定时规则','schedule.edit':'编辑定时规则','schedule.enable':'启用定时规则','schedule.disable':'停用定时规则','schedule.delete':'删除定时规则','schedule.execute':'定时额度执行'};
  const money=value=>Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const time=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  let cursors=[null],page=0,next=null,busy=false,epoch=0,detailBusy=false,detailEpoch=0,current=null,after=null;
  function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
  function cell(tr,text){const td=node('td',text);tr.append(td);return td;}
  function userIdentity(user={}){
    const hasId=user.id!==undefined&&user.id!==null&&Number(user.id)>0;
    return user.username?`${user.username}（${hasId?'ID '+user.id:'ID 未返回'}）`:hasId?`用户 ID ${user.id}`:'目标用户未知（ID 未返回）';
  }
  function ruleIdentity(rule){
    const groups=(rule.groups||[]).map(group=>group||'未分组');
    return `规则 ID ${rule.id}（${groups.length?'用户组 '+groups.join('、'):'用户组未记录'}）`;
  }
  function actionLabel(row){
    let target;
    if(row.target_rule)target=ruleIdentity(row.target_rule);
    else{
      const users=row.target_users||[];
      target=users.length?users.map(userIdentity).join('、'):'目标用户未知';
      if(row.target_user_count>users.length)target+=`等 ${row.target_user_count} 位用户`;
    }
    return (labels[row.action]||row.action)+' · '+target;
  }
  function safe(value){const v={...value};for(const k of ['password','password_confirm','key','access_token','target_user'])delete v[k];return v;}
  function summary(value){const v=safe(value);if(v.amount_units!==undefined){v.amount_yuan='¥ '+money(Number(v.amount_units)/500000);delete v.amount_units;}if(v.quota!==undefined){v.balance='¥ '+money(Number(v.quota)/500000);delete v.quota;}return Object.keys(v).length?JSON.stringify(v):'—';}
  async function api(path){const response=await fetch('/cockpit/operations/api/records'+path,{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||'操作记录读取失败。');return data;}
  function controls(){$('records-prev').disabled=busy||page===0;$('records-next').disabled=busy||!next;$('records-refresh').disabled=busy;$('records-kind').disabled=busy;}
  async function load(){
    if(busy)return;busy=true;controls();const ticket=++epoch;
    try{
      const params=new URLSearchParams({kind:$('records-kind').value||''});if(cursors[page])params.set('before',cursors[page]);
      const data=await api('?'+params);if(ticket!==epoch)return;
      $('records-rows').replaceChildren();
      for(const row of data.rows){const tr=node('tr'),counts=row.counts||{};for(const text of [time(row.occurred_at),row.operator_name,actionLabel(row),summary(row.parameters),states[row.state]||row.state,`${counts.success||0} / ${counts.failed||0} / ${counts.uncertain||0}`])cell(tr,text);const b=node('button','详情');b.type='button';b.addEventListener('click',()=>show(row));cell(tr,'').append(b);$('records-rows').append(tr);}
      if(!data.rows.length){const tr=node('tr');cell(tr,'暂无操作记录').colSpan=7;$('records-rows').append(tr);}
      next=data.next_before;$('records-page').textContent=`第 ${page+1} 页`;$('records-status').textContent='结果不明确或请求中，请核对 New API 的实际数据，不要直接重试。';
    }catch(e){$('records-status').textContent=e.message;}
    finally{busy=false;controls();}
  }
  async function show(row){current=row;after=null;detailEpoch++;$('records-title').textContent=actionLabel(row);$('records-items').replaceChildren();$('records-dialog').showModal();await items(false);}
  async function items(more=true){
    if(more&&detailBusy)return;detailBusy=true;const ticket=++detailEpoch,record=current;$('records-items-more').disabled=true;$('records-detail-status').textContent='正在读取…';
    try{
      const data=await api('/'+record.source+'/'+encodeURIComponent(record.id)+(more&&after!==null?'?after='+after:''));if(ticket!==detailEpoch)return;
      for(const row of data.rows){const tr=node('tr'),type={user:'用户',token:'KEY',quota_rule:'定时规则'}[row.target_type]||row.target_type,object=type+' / '+(row.target_id>0?'ID '+row.target_id:'ID 未返回'),target=row.target_rule?ruleIdentity(row.target_rule):userIdentity(row.target_user);for(const text of [object,target,summary(row.before_data),summary(row.after_data),states[row.state]||row.state,row.message])cell(tr,text);$('records-items').append(tr);}
      after=data.next_after;$('records-items-more').hidden=after===null;$('records-detail-status').textContent='操作详情已读取。';
    }catch(e){if(ticket===detailEpoch)$('records-detail-status').textContent=e.message;}
    finally{if(ticket===detailEpoch){detailBusy=false;$('records-items-more').disabled=false;}}
  }
  $('records-refresh').addEventListener('click',()=>{if(!busy){cursors=[null];page=0;load();}});
  $('records-kind').addEventListener('change',()=>{if(!busy){cursors=[null];page=0;load();}});
  $('records-prev').addEventListener('click',()=>{if(!busy&&page>0){page--;load();}});
  $('records-next').addEventListener('click',()=>{if(!busy&&next){cursors[++page]=next;load();}});
  $('records-items-more').addEventListener('click',()=>items());
  $('records-close').addEventListener('click',()=>$('records-dialog').close());
  $('records-dialog').addEventListener('close',()=>{detailEpoch++;detailBusy=false;current=null;});
  load();
})();
