/* Independent quota schedules; all writes require authenticated administrator confirmation. */
(() => {
  const $ = id => document.getElementById(id);
  const periods={daily:'每天',weekly:'每周',monthly:'每月'};
  const statuses={running:'执行中',success:'成功',partial:'部分成功',failed:'失败',unknown:'结果不明确',sending:'请求中'};
  const money=value=>value===null||value===undefined?'—':Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const time=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  const groupName=value=>value||'未分组';
  const operation=value=>value==='add'?'增加':'减少';
  let data={configured:false,rows:[],groups:[]},editing=null,busy=false,needsRefresh=false;
  const cell=(tr,value)=>{const td=document.createElement('td');td.textContent=value;tr.append(td);return td;};
  const status=(value,error=false)=>{$('schedule-status').textContent=value;$('schedule-status').className=error?'error':'';};
  async function api(path,method='GET',body){
    const options={cache:'no-store',method};
    if(method!=='GET')Object.assign(options,{headers:{'Content-Type':'application/json','X-Quota-Action':'schedule'},body:JSON.stringify(body)});
    let response;try{response=await fetch('/cockpit/api/users/'+path,options);}catch{const error=new Error('网络中断，请刷新核对结果，不要重复提交。');error.ambiguous=true;throw error;}
    let result;try{result=await response.json();}catch{const error=new Error('服务响应无效，请刷新核对结果，不要重复提交。');error.ambiguous=true;throw error;}
    if(!response.ok){const error=new Error(result.error||`请求失败（HTTP ${response.status}）`);error.ambiguous=response.status>=500||response.status===409;throw error;}
    return result;
  }
  function controls(){
    $('schedule-close').disabled=busy;$('schedule-save').disabled=busy||needsRefresh;
    $('schedule-new').disabled=busy||needsRefresh||!data.configured;$('schedule-refresh').disabled=busy;
    $('schedule-edit-cancel').disabled=busy;
  }
  function button(parent,label,callback,disabled=false){
    const button=document.createElement('button');button.type='button';button.textContent=label;button.disabled=disabled;
    button.addEventListener('click',callback);parent.append(button);return button;
  }
  function renderRules(){
    $('schedule-rules').replaceChildren();
    for(const rule of data.rows){
      const tr=document.createElement('tr');
      for(const value of [rule.enabled?'启用':'停用',rule.groups.map(groupName).join('、'),periods[rule.period],operation(rule.operation),money(rule.amount_yuan),rule.executor_username,time(rule.next_run_at),statuses[rule.last_status]||'—'])cell(tr,value);
      const actions=cell(tr,'');
      button(actions,'编辑',()=>edit(rule),busy||needsRefresh||!rule.can_edit);
      button(actions,rule.enabled?'停用':'启用',()=>toggle(rule),busy||needsRefresh||!rule.can_edit);
      button(actions,'删除',()=>remove(rule),busy||needsRefresh||!rule.can_edit).className='schedule-danger';
      $('schedule-rules').append(tr);
    }
    if(!data.rows.length){const tr=document.createElement('tr');cell(tr,'暂无定时规则').colSpan=9;$('schedule-rules').append(tr);}
    controls();
  }
  async function loadRules(){
    data=await api('schedules');needsRefresh=false;renderRules();
    status(data.configured?'保存/重新启用规则后，从下一周期执行；定时器随服务自动启动，无需单独部署。':'未配置监控数据库，定时额度功能不可用。',!data.configured);
  }
  function edit(rule=null){
    if(busy||needsRefresh||!data.configured)return;
    editing=rule;$('schedule-form').hidden=false;$('schedule-form-title').textContent=rule?'编辑规则 #'+rule.id:'新增规则';
    $('schedule-period').value=rule?.period||'daily';$('schedule-operation').value=rule?.operation||'add';
    $('schedule-amount').value=rule?.amount_yuan||'';$('schedule-enabled').checked=rule?.enabled||false;
    $('schedule-executor').textContent=`保存后执行管理员为 ${data.current_username}（ID ${data.current_user_id}）；执行时从 New API 读取该管理员最新 PAT。`;
    const groups=[...new Set([...data.groups,...(rule?.groups||[])])].sort();$('schedule-groups').replaceChildren();
    for(const group of groups){
      const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=group;input.checked=!!rule?.groups.includes(group);
      label.append(input,document.createTextNode(groupName(group)+(data.groups.includes(group)?'':'（已不存在）')));$('schedule-groups').append(label);
    }
    $('schedule-form').scrollIntoView?.({block:'nearest'});
  }
  async function write(action){
    if(busy||needsRefresh)return;
    busy=true;controls();renderRules();
    try{await action();$('schedule-form').hidden=true;editing=null;await loadRules();}
    catch(error){needsRefresh=!!error.ambiguous;status(error.message+(needsRefresh?' 请先刷新规则，避免创建重复规则。':''),true);}
    finally{busy=false;renderRules();}
  }
  async function toggle(rule){
    if(busy||needsRefresh)return;
    if(!window.confirm(rule.enabled?'停用该规则？已经开始的执行不会回滚。':`启用该规则？${rule.groups.map(groupName).join('、')} 中每位启用用户将按周期${operation(rule.operation)} ${money(rule.amount_yuan)} 元。执行管理员将设为 ${data.current_username}。重叠规则会累计执行。`))return;
    await write(()=>api('schedules/'+rule.id,'PATCH',{version:rule.version,enabled:!rule.enabled}));
  }
  async function remove(rule){
    if(busy||needsRefresh||!window.confirm('删除规则 #'+rule.id+'？历史执行记录保留，已经开始的执行不会回滚。'))return;
    await write(()=>api('schedules/'+rule.id,'DELETE',{version:rule.version}));
  }
  $('schedule-form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||needsRefresh)return;
    const groups=[...$('schedule-groups').querySelectorAll('input:checked')].map(input=>input.value);
    if(!groups.length){status('请至少勾选一个用户组。',true);return;}
    if(!$('schedule-form').checkValidity())return;
    const body={groups,period:$('schedule-period').value,operation:$('schedule-operation').value,
      amount_yuan:$('schedule-amount').value,enabled:$('schedule-enabled').checked};
    if(editing)body.version=editing.version;
    const overlap=data.rows.filter(r=>r.id!==editing?.id&&r.enabled&&r.groups.some(g=>groups.includes(g)));
    const warning=overlap.length?'\n注意：这些用户组已有启用规则，不同规则会累计增减。':'';
    if(!window.confirm(`确认保存？${groups.map(groupName).join('、')}，${periods[body.period]}每人${operation(body.operation)} ${money(body.amount_yuan)} 元；${body.enabled?'启用':'停用'}。保存不会立即修改额度，减少额度允许负余额。${warning}`))return;
    await write(()=>api('schedules'+(editing?'/'+editing.id:''),editing?'PUT':'POST',body));
  });
  $('quota-settings').addEventListener('click',async()=>{
    if(busy)return;await $('quota-settings-dialog').showModal();busy=true;controls();status('正在读取…');
    try{await loadRules();}catch(error){status(error.message,true);}finally{busy=false;renderRules();}
  });
  $('schedule-close').addEventListener('click',()=>{if(!busy)$('quota-settings-dialog').close();});
  $('quota-settings-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  $('schedule-new').addEventListener('click',()=>edit());
  $('schedule-edit-cancel').addEventListener('click',()=>{if(!busy){editing=null;$('schedule-form').hidden=true;}});
  $('schedule-refresh').addEventListener('click',async()=>{
    if(busy)return;busy=true;controls();
    try{await loadRules();editing=null;$('schedule-form').hidden=true;}catch(error){status(error.message,true);}finally{busy=false;renderRules();}
  });
})();
