/* Balance settings and archives belong to the current ledger; notification settings are global. */
let balanceData=null,balanceVersion=null;
let channelVersion=null,channelDirty=false,channelBusy=false;
let channelLoaded=null,channelRequest=0;
const channelInputs=['channel-enabled','channel-app-id','channel-secret','channel-receive-type','channel-receive-id','channel-webhook-url','channel-signing-enabled','channel-signing-secret',
  'channel-email-host','channel-email-port','channel-email-security','channel-email-auth-enabled','channel-email-username','channel-email-password','channel-email-from-address','channel-email-from-name','channel-email-recipients'];
let historyPreview=null;
const balanceMoney=value=>value===null||value===undefined?'—':'¥ '+number(value,2);
const balanceTableMoney=value=>value===null||value===undefined?'—':'¥ '+Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const balanceTime=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
async function balanceRequest(path,options={}){
  const root=!path||path.startsWith('?')?'/cockpit/api/statistics/balance/status':'/cockpit/api/statistics/balance';
  const globalChannel=path.startsWith('/channel');
  const response=await (globalChannel?fetch(root+path,options):Scope.request(root+path,options));
  let data;try{data=await response.json();}catch(error){if(error.name==='AbortError')throw error;throw new Error('监控服务暂不可用，请稍后重试。');}
  if(!response.ok)throw new Error(data.error||'监控请求失败。');
  return data;
}
function balanceWrite(method,body){return {method,headers:{'Content-Type':'application/json','X-Statistics-Request':'1'},body:JSON.stringify(body)};}
function balanceSettingsBody(){
  return {enabled:$('balance-enabled').checked,budget:$('balance-budget').value,threshold:$('balance-threshold').value,start_month:$('balance-start').value,version:balanceVersion};
}
function renderBalance(){
  const data=balanceData;
  $('balance-summary').replaceChildren();$('balance-months').replaceChildren();$('balance-alert-list').replaceChildren();
  if(!data.configured){$('balance-status').textContent='余额监控数据库尚未配置。';return;}
  const s=data.settings,state=data.state;
  $('balance-bell').classList.toggle('alert-active',s.enabled&&data.alerts.some(a=>!a.resolved_at));
  $('balance-bell').setAttribute('aria-label',s.enabled&&data.alerts.some(a=>!a.resolved_at)?'余额警报：余额不足':'余额警报');
  $('balance-status').className=data.stale&&s.enabled?'error':'';
  $('balance-status').textContent=!s.enabled?'监控未启用':!data.valid?'等待定时任务更新':
    `截至 ${balanceTime(state.checked_at)}（北京时间）${data.stale?' · 数据更新延迟':''}`;
  if(s.enabled&&state?.last_error)$('balance-status').textContent+=' · '+state.last_error;
  for(const [label,value] of [['总额度',s.budget],['已归档消费',data.valid?state.archived_amount:null],['本月消费',data.valid?state.current_amount:null],['剩余额度',data.valid?state.remaining:null]]){
    const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=balanceMoney(value);div.append(dt,dd);$('balance-summary').append(div);
  }
  for(const a of (s.enabled?data.alerts:[])){
    const div=document.createElement('div');div.className='balance-alert';
    const title=document.createElement('strong');title.textContent=a.resolved_at?'余额不足 · 已解除':'余额不足 · 活动中';
    const detail=document.createElement('p');detail.textContent=`剩余 ${balanceMoney(a.remaining)}，${a.resolved_at?'报警阈值':'低于阈值'} ${balanceMoney(a.threshold)}`;
    const time=document.createElement('p');time.textContent=`检查时间：${balanceTime(a.updated_at)}`;
    div.append(title,detail,time);
    $('balance-alert-list').append(div);
  }
  for(const m of data.months){const tr=document.createElement('tr');cell(tr,m.month.slice(0,7));cell(tr,balanceTableMoney(m.amount));$('balance-months').append(tr);}
  if(!data.months.length){const tr=document.createElement('tr');cell(tr,'暂无月度归档').colSpan=2;$('balance-months').append(tr);}
}
async function refreshBalance(live=false){
  const ticket=Scope.epoch;
  try{
    const data=await balanceRequest(live?'?live=1':'');Scope.guard(ticket);
    balanceData=data;renderBalance();$('balance-bell').title='余额警报';return data;
  }catch(e){if(ticket===Scope.epoch){$('balance-status').textContent=e.message;$('balance-status').className='error';$('balance-bell').title='余额监控查询失败';}throw e;}
}
function setSettingsTab(tab){
  document.querySelectorAll('[data-settings-tab]').forEach(button=>{
    const active=button.dataset.settingsTab===tab;
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    $(`settings-panel-${button.dataset.settingsTab}`).hidden=!active;
  });
}
document.querySelectorAll('[data-settings-tab]').forEach(button=>button.addEventListener('click',()=>setSettingsTab(button.dataset.settingsTab)));
function renderUsageChannels(rows){
  $('usage-channel-options').replaceChildren();
  if(!rows.length){const empty=document.createElement('span');empty.className='usage-channel-empty';empty.textContent='当前账本暂无渠道';$('usage-channel-options').append(empty);return;}
  for(const row of rows){
    const label=document.createElement('div'),name=document.createElement('span'),status=document.createElement('span');
    label.className='channel-row';
    name.className='usage-channel-name';name.textContent=`ID ${row.channel_id} · ${row.channel_name}`;
    status.className='usage-channel-status '+(row.deleted?'deleted':Number(row.channel_status)===1?'enabled':'disabled');
    status.textContent=row.deleted?'已删除':Number(row.channel_status)===1?'已启用':'已禁用';
    label.append(name,status);$('usage-channel-options').append(label);
  }
}
async function loadUsageChannels(){
  const ticket=Scope.epoch;
  const data=await balanceRequest('/usage-channels');Scope.guard(ticket);renderUsageChannels(data.rows||[]);
}
async function openBalanceSettings(){
  if(!Scope.current)return;
  const ticket=Scope.epoch;
  setSettingsTab('balance');$('balance-settings').showModal();
  $('balance-save').disabled=true;$('balance-recalculate').disabled=true;
  $('balance-settings-status').textContent='正在读取当前账本设置…';
  $('usage-channel-options').replaceChildren();
  $('balance-enabled').checked=false;
  for(const id of ['balance-budget','balance-threshold','balance-start'])$(id).value='';
  // Global notification form is not reset by ledger changes.
  if(channelVersion===null)loadChannel();
  try{
    const data=await refreshBalance();Scope.guard(ticket);
    if(!data?.configured){$('balance-settings-status').textContent='余额监控数据库尚未配置，请稍后重试。';return;}
    const settings=data.settings;balanceVersion=settings.version;
    $('balance-enabled').checked=settings.enabled;$('balance-budget').value=settings.budget;
    $('balance-threshold').value=settings.threshold;$('balance-start').value=(settings.start_month||'').slice(0,7);
    await loadUsageChannels();Scope.guard(ticket);
    $('balance-settings-status').textContent='';$('balance-save').disabled=false;$('balance-recalculate').disabled=false;
  }catch(e){if(ticket===Scope.epoch)$('balance-settings-status').textContent=e.message;}
}
$('balance-gear').addEventListener('click',openBalanceSettings);
$('balance-bell').addEventListener('click',async()=>{
  if(!Scope.current)return;
  const ticket=Scope.epoch;
  $('balance-alerts').showModal();$('balance-bell').disabled=true;
  $('balance-status').className='';$('balance-status').textContent='正在读取当前账本…';
  try{
    // Read enabled state first. Disabled ledgers must never trigger a check/notification.
    const data=await refreshBalance();Scope.guard(ticket);
    if(data.configured&&data.settings.enabled){
      balanceData=await balanceRequest('/check',balanceWrite('POST',{}));Scope.guard(ticket);renderBalance();
    }
  }catch(e){if(ticket===Scope.epoch){$('balance-status').textContent=e.message;$('balance-status').className='error';}}
  finally{if(ticket===Scope.epoch)$('balance-bell').disabled=false;}
});
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
$('balance-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!Scope.current||balanceVersion===null||$('balance-save').disabled)return;const ticket=Scope.epoch;$('balance-save').disabled=true;
  $('balance-settings-status').textContent='正在保存设置并归档历史月份…';
  try{
    await balanceRequest('/settings',balanceWrite('PUT',balanceSettingsBody()));
    Scope.guard(ticket);$('balance-settings').close();$('balance-alerts').showModal();await refreshBalance();
  }catch(e){if(ticket===Scope.epoch)$('balance-settings-status').textContent=e.message;}
  finally{if(ticket===Scope.epoch)$('balance-save').disabled=false;}
});
$('balance-recalculate').addEventListener('click',async()=>{
  const ticket=Scope.epoch;
  const warnings=[
    '追溯会重建所选起始月份至上月所有渠道、所有分组及“全部”账本的归档，不仅影响当前账本。是否继续？',
    '渠道启用状态不影响原始归档；如果查询失败或整个区间没有数据，系统会报错并保留旧归档。再次确认？',
    '预览只展示当前账本的逐月金额对比，但确认写入将影响所有账本。确定生成预览吗？',
  ];
  if(warnings.some(message=>!window.confirm(message)))return;
  $('balance-recalculate').disabled=true;$('balance-save').disabled=true;
  $('balance-settings-status').textContent='正在读取完整历史渠道计费，请勿关闭页面…';
  try{
    const settings=balanceSettingsBody();
    const result=await balanceRequest('/recalculate-history/preview',balanceWrite('POST',settings));
    Scope.guard(ticket);historyPreview={settings,rows:result.rows,scope_id:Scope.current.id};
    $('balance-history-preview-rows').replaceChildren();
    for(const row of result.rows){
      const tr=document.createElement('tr');
      cell(tr,row.month);cell(tr,balanceTableMoney(row.before));cell(tr,balanceTableMoney(row.after));cell(tr,balanceTableMoney(Number(row.after)-Number(row.before)));
      $('balance-history-preview-rows').append(tr);
    }
    $('balance-history-preview-status').textContent='';
    $('balance-settings-status').textContent='预览仅展示当前账本；确认后将重建所选月份的所有渠道及分组归档。';
    $('balance-history-preview').showModal();
  }catch(e){if(ticket===Scope.epoch)$('balance-settings-status').textContent=e.message;}
  finally{if(ticket===Scope.epoch){$('balance-recalculate').disabled=false;$('balance-save').disabled=false;}}
});
$('balance-history-apply').addEventListener('click',async()=>{
  if(!historyPreview||historyPreview.scope_id!==Scope.current?.id)return;
  const ticket=Scope.epoch;
  if(!window.confirm('确认重建所选起始月份至上月所有渠道、所有分组及“全部”账本归档？本页对比仅包含当前账本，其他账本的历史金额和余额也可能变化。'))return;
  $('balance-history-apply').disabled=true;
  $('balance-history-preview-status').textContent='正在再次核对并写入完整渠道归档…';
  try{
    const result=await balanceRequest('/recalculate-history',balanceWrite('POST',{settings:historyPreview.settings,preview:historyPreview.rows}));
    Scope.guard(ticket);balanceVersion=result.version;historyPreview=null;
    $('balance-history-preview').close();$('balance-settings').close();$('balance-alerts').showModal();
    await refreshBalance(true);
  }catch(e){if(ticket===Scope.epoch)$('balance-history-preview-status').textContent=e.message;}
  finally{if(ticket===Scope.epoch)$('balance-history-apply').disabled=false;}
});
$('balance-history-preview').addEventListener('close',()=>{historyPreview=null;$('balance-history-preview-status').textContent='';});
function channelButtons(){
  for(const id of channelInputs){
    $(id).disabled=channelBusy||channelVersion===null;
  }
  $('channel-type').disabled=channelBusy;
  $('channel-save').disabled=channelBusy||channelVersion===null;
  $('channel-test').disabled=channelBusy||channelVersion===null||channelDirty;
}
function channelFields(channel,receiveType){
  for(const [name,id] of [['feishu_app','channel-feishu-fields'],['dingtalk_webhook','channel-dingtalk-fields'],['email','channel-email-fields']])$(id).hidden=channel!==name;
  const options=[['chat_id','群聊（chat_id）'],['user_id','个人（user_id）']];
  $('channel-receive-type').replaceChildren(...options.map(([value,label])=>new Option(label,value)));
  if(options.some(([value])=>value===receiveType))$('channel-receive-type').value=receiveType;
}
function emailFields(){
  $('channel-email-auth-fields').hidden=!$('channel-email-auth-enabled').checked;
  $('channel-email-warning').hidden=$('channel-email-security').value!=='smtp';
}
function renderChannel(data){
  channelVersion=data.version;channelLoaded=data.channel;channelDirty=false;
  $('channel-enabled').checked=data.enabled;$('channel-type').value=data.channel;
  channelFields(data.channel,data.receive_id_type);
  $('channel-app-id').value=data.app_id||'';$('channel-secret').value='';
  $('channel-secret').placeholder=data.secret_configured?'已保存；留空保持不变':'未配置';
  $('channel-receive-type').value=data.receive_id_type||'chat_id';$('channel-receive-id').value=data.receive_id||'';
  $('channel-webhook-url').value='';$('channel-webhook-url').placeholder=data.webhook_configured?'已保存；留空保持不变':'未配置';
  $('channel-signing-enabled').checked=Boolean(data.signing_enabled);$('channel-signing-secret').value='';
  $('channel-signing-secret').placeholder=data.signing_secret_configured?'已保存；留空保持不变':'未配置';
  $('channel-signing-secret-field').hidden=!$('channel-signing-enabled').checked;
  $('channel-email-host').value=data.smtp_host||'';$('channel-email-port').value=data.smtp_port??587;
  $('channel-email-security').value=data.smtp_security||'starttls';
  $('channel-email-auth-enabled').checked=Boolean(data.auth_enabled);
  $('channel-email-username').value=data.username||'';$('channel-email-password').value='';
  $('channel-email-password').placeholder=data.password_configured?'已保存；留空保持不变':'未配置';
  $('channel-email-from-address').value=data.from_address||'';$('channel-email-from-name').value=data.from_name||'';
  $('channel-email-recipients').value=(data.recipients||[]).join('\n');emailFields();
  $('channel-status').textContent=data.last_error?`最近发送失败：${data.last_error}`:
    data.last_success_at?`最近发送成功：${balanceTime(data.last_success_at)}`:'';
  channelButtons();
}
async function loadChannel(channel=$('channel-type').value||'feishu_app'){
  const ticket=++channelRequest;channelBusy=true;channelVersion=null;channelLoaded=null;channelDirty=false;
  channelButtons();$('channel-status').textContent='正在读取渠道配置…';
  try{
    const data=await balanceRequest('/channel?channel='+encodeURIComponent(channel));
    if(ticket!==channelRequest)return;
    if(data.channel!==channel||!Number.isInteger(data.version))throw new Error('渠道配置不匹配，请重新选择渠道。');
    renderChannel(data);
  }catch(e){if(ticket===channelRequest)$('channel-status').textContent=e.message;}
  finally{if(ticket===channelRequest){channelBusy=false;channelButtons();}}
}
$('channel-type').addEventListener('change',()=>loadChannel($('channel-type').value));
$('channel-form').addEventListener('input',()=>{channelDirty=true;channelButtons();});
$('channel-signing-enabled').addEventListener('change',()=>{$('channel-signing-secret-field').hidden=!$('channel-signing-enabled').checked;});
$('channel-email-auth-enabled').addEventListener('change',emailFields);
$('channel-email-security').addEventListener('change',()=>{
  const defaults={smtp:25,starttls:587,smtps:465},port=Number($('channel-email-port').value);
  if(!port||Object.values(defaults).includes(port))$('channel-email-port').value=defaults[$('channel-email-security').value];
  emailFields();
});
$('channel-form').addEventListener('submit',async event=>{
  event.preventDefault();if(channelBusy||channelVersion===null||channelLoaded!==$('channel-type').value)return;
  channelBusy=true;channelButtons();$('channel-status').textContent='正在保存渠道…';
  try{
    const body={version:channelVersion,enabled:$('channel-enabled').checked,channel:$('channel-type').value};
    if(body.channel==='dingtalk_webhook')Object.assign(body,{webhook_url:$('channel-webhook-url').value,
      signing_enabled:$('channel-signing-enabled').checked,signing_secret:$('channel-signing-secret').value});
    else if(body.channel==='email')Object.assign(body,{smtp_host:$('channel-email-host').value,smtp_port:Number($('channel-email-port').value),
      smtp_security:$('channel-email-security').value,auth_enabled:$('channel-email-auth-enabled').checked,
      username:$('channel-email-username').value,password:$('channel-email-password').value,
      from_address:$('channel-email-from-address').value,from_name:$('channel-email-from-name').value,
      recipients:$('channel-email-recipients').value.split(/[\n,;，；]+/).map(value=>value.trim()).filter(Boolean)});
    else Object.assign(body,{app_id:$('channel-app-id').value,app_secret:$('channel-secret').value,
      receive_id_type:$('channel-receive-type').value,receive_id:$('channel-receive-id').value});
    const data=await balanceRequest('/channel',balanceWrite('PUT',body));
    renderChannel(data);$('channel-status').textContent='渠道已保存。';
  }catch(e){$('channel-status').textContent=e.message;}
  finally{channelBusy=false;channelButtons();}
});
$('channel-test').addEventListener('click',async()=>{
  if(channelBusy||channelDirty||channelVersion===null||channelLoaded!==$('channel-type').value)return;
  channelBusy=true;channelButtons();$('channel-status').textContent='正在发送测试消息…';
  try{
    await balanceRequest('/channel/test',balanceWrite('POST',{channel:channelLoaded,version:channelVersion}));
    $('channel-status').textContent='测试消息已发送。';
  }catch(e){$('channel-status').textContent=e.message;}
  finally{channelBusy=false;channelButtons();}
});
window.addEventListener('scopechange',()=>{
  balanceData=null;balanceVersion=null;historyPreview=null;
  for(const id of ['balance-settings','balance-alerts','balance-history-preview'])$(id).close();
  for(const id of ['balance-summary','balance-months','balance-alert-list','usage-channel-options','balance-history-preview-rows'])$(id).replaceChildren();
  const name=Scope.name(Scope.current);
  $('balance-settings-title').textContent='设置';$('balance-scope-label').textContent='当前账本：'+name+'（独立额度与监控设置）';
  $('balance-alerts-title').textContent=name+' · 余额与警报';
  $('balance-history-preview-title').textContent=name+' · 历史计费对比';
  $('balance-bell').classList.remove('alert-active');$('balance-bell').setAttribute('aria-label','余额警报');
  $('balance-bell').disabled=false;$('balance-gear').disabled=false;
  $('balance-history-apply').disabled=false;$('balance-save').disabled=true;
  $('balance-status').textContent='正在读取当前账本…';
  refreshBalance().catch(()=>{});
});
$('balance-bell').disabled=true;$('balance-gear').disabled=true;
channelButtons();
