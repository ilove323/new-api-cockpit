(function(root){
  'use strict';
  const states={preview:'待确认',running:'执行中',completed:'全部成功',partial:'部分完成',cancelled:'已取消',sending:'请求中（中断需核对）',success:'成功',failed:'失败',unknown:'结果不明确',conflict:'配置冲突'};
  const tokenStates={1:'启用',2:'禁用',3:'已过期',4:'额度耗尽'};
  function money(value){if(value===null||value===undefined||value==='')return '—';const n=Number(value);return Number.isFinite(n)?n.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'—';}
  function groupName(value){return value===''?'跟随用户组':value==='auto'?'自动分组':value||'—';}
  function keywordMatch(label,term){return String(label).toLowerCase().includes(String(term).toLowerCase());}
  function groupChange(group){return {group};}
  function groupChoices(groups){return [...new Set(['','auto',...groups])];}
  function safeHistory(value){const copy={...value};for(const key of ['password','password_confirm','key','access_token'])delete copy[key];return copy;}
  const helpers={money,groupName,keywordMatch,groupChange,groupChoices,safeHistory};
  if(typeof module!=='undefined'&&module.exports)module.exports=helpers;
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);
  const el=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(className)n.className=className;return n;};
  let page=1,total=0,totalKeys=0,rows=[],selected=new Set(),allFiltered=false,userId=null,queryVersion=0,preview=null,running=false,edit=null;
  let pendingGroupChange=null,groupSaving=false,managementWritable=false;
  const status=(message,error=false)=>{$('management-status').textContent=message;$('management-status').classList.toggle('error',error);};
  async function api(path,body){const init=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Management-Action':'confirm'},body:JSON.stringify(body)};let response;try{response=await fetch('/cockpit/api/keys/'+path,init);}catch{throw new Error('连接中断；已提交的修改可能成功，请核对操作记录，不要重复提交。');}let data;try{data=await response.json();}catch{throw new Error('响应无法读取，请核对实际结果，不要重复提交。');}if(!response.ok)throw new Error(data.error||'请求失败，请核对结果。');return data;}
  const checked=id=>Array.from($(id).querySelectorAll('input[type=checkbox]:checked')).map(n=>n.value);
  function filters(){const ug=checked('user-group-options'),tg=checked('token-group-options');return {page,page_size:50,search:$('management-keyword').value,user_statuses:checked('user-status-options').map(Number),user_groups:ug.length?ug:null,token_groups:tg.length?tg:null,token_statuses:checked('token-status-options').map(Number),...(userId===null?{}:{user_id:userId})};}
  function invalidate(){preview=null;selected.clear();allFiltered=false;selectionLabel();if($('batch-dialog').open&&!running)$('batch-dialog').close();if($('key-group-dialog').open&&!groupSaving)$('key-group-dialog').close();}
  function selectionLabel(){$('token-selection-count').textContent=allFiltered?`已选全部筛选结果（${totalKeys} 个 KEY）`:`已选 ${selected.size} 个 KEY`;}
  function time(value,epoch=false){if(value===-1||value===null||value===undefined)return '永不过期';const d=new Date(epoch?Number(value)*1000:value);return Number.isNaN(d.valueOf())?'—':d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});}
  function actionButton(label,callback,danger=false){
    const icons={'详情':'info','编辑':'pencil','额度':'wallet','查看 / 复制':'eye','启用':'power','禁用':'power-off','删除':'trash-2'};
    const icon=icons[label],n=el('button',label,(icon?'action-icon':'button-small')+(danger?' danger':''));n.type='button';
    if(icon){n.dataset.icon=icon;n.setAttribute('aria-label',label);n.title=label;}
    n.addEventListener('click',()=>Promise.resolve(callback()).catch(e=>status(e.message,true)));return n;
  }
  function cell(tr,value,className){const n=el('td',value,className);tr.append(n);return n;}
  async function query(){const seq=++queryVersion;try{status('正在读取…');const data=await api('query/grouped',filters());if(seq!==queryVersion)return;rows=data.rows;total=data.total;totalKeys=data.total_keys;render();$('page-info').textContent=`第 ${page} 页 / ${Math.max(1,Math.ceil(total/50))} 页，共 ${total} 个用户 / ${totalKeys} 个 KEY（按用户分页）`;$('page-prev').disabled=page===1;$('page-next').disabled=page*50>=total;selectionLabel();status('列表已更新；KEY 只显示脱敏信息。');}catch(e){if(seq===queryVersion)status(e.message,true);}}
  const visibleTokens=()=>rows.flatMap(user=>user.tokens);
  function keyGroupPicker(row,user){
    const picker=el('details',undefined,'key-group-picker');
    const trigger=el('summary',groupName(row.token_group),'key-group-trigger');
    trigger.title='点击切换分组';
    trigger.setAttribute('aria-label',`切换 KEY ${row.id} 的分组（${groupName(row.token_group)}）`);
    const menu=el('div',undefined,'key-group-menu');menu.setAttribute('role','group');menu.setAttribute('aria-label','选择目标 KEY 分组');
    picker.append(trigger,menu);let version=0;
    trigger.addEventListener('click',event=>{if(running||groupSaving)event.preventDefault();});
    function position(){
      const rect=trigger.getBoundingClientRect(),width=Math.min(280,window.innerWidth-16);
      const below=window.innerHeight-rect.bottom-8,above=rect.top-8,up=below<160&&above>below;
      menu.style.width=width+'px';menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';
      menu.style.top=up?'auto':Math.max(8,rect.bottom+6)+'px';menu.style.bottom=up?Math.max(8,window.innerHeight-rect.top+6)+'px':'auto';
      menu.style.maxHeight=Math.max(0,Math.min(320,(up?above:below)-6))+'px';
    }
    picker.addEventListener('toggle',async()=>{
      const seq=++version;if(!picker.open)return;
      menu.replaceChildren(el('p','正在读取可用分组…','hint'));position();
      try{
        const data=await api(row.id+'/groups');
        if(seq!==version||!picker.open||!picker.isConnected)return;
        if(data.user_status!==1){menu.replaceChildren(el('p','所属用户已禁用，不能更改 KEY 分组。','error'));return;}
        if(!managementWritable){menu.replaceChildren(el('p','未配置监控数据库，只可查询，不能保存分组修改。','error'));return;}
        menu.replaceChildren(el('p','当前分组：'+groupName(data.group),'hint'));
        const search=el('input');search.type='search';search.autocomplete='off';search.placeholder='输入关键词筛选分组';search.setAttribute('aria-label','筛选目标 KEY 分组');menu.append(search);
        const options=[];
        for(const group of groupChoices(data.available_groups)){
          const button=el('button',groupName(group),'key-group-option');button.type='button';button.dataset.group=group;button.disabled=group===data.group;
          if(button.disabled){button.classList.add('is-current');button.title='当前分组';}
          button.addEventListener('click',()=>{
            if(running||groupSaving||group===data.group)return;
            picker.open=false;pendingGroupChange={id:row.id,group,trigger,submitted:false};
            $('key-group-summary').textContent=`用户 ${user.username} · KEY ${row.name||'（未命名）'}（ID ${row.id}，${row.masked_key}）\n${groupName(data.group)} → ${groupName(group)}`;
            $('key-group-error').textContent='';$('key-group-save').disabled=false;$('key-group-cancel').disabled=false;
            $('key-group-dialog').showModal();$('key-group-cancel').focus();
          });
          menu.append(button);options.push(button);
        }
        const empty=el('p','没有匹配的分组。','hint');empty.hidden=true;menu.append(empty);
        search.addEventListener('input',()=>{for(const button of options)button.hidden=!keywordMatch(button.textContent,search.value);empty.hidden=options.some(button=>!button.hidden);});
        search.focus();
      }catch(error){if(seq===version&&picker.open&&picker.isConnected)menu.replaceChildren(el('p',error.message,'error'));}
    });
    return picker;
  }
  function userCell(tr,user,count){
    const owner=cell(tr,'','user-cell');owner.rowSpan=count;
    owner.append(el('strong',user.username,'user-name'));
    if(user.display_name)owner.append(el('span',user.display_name,'display-name'));
    owner.append(el('span',`ID ${user.id} · ${user.status===1?'启用':'禁用'} · 用户组：${user.user_group||'—'}`,'user-meta'));
    owner.append(el('span',`KEY：${user.tokens.length} 个匹配结果`,'user-meta'));
    const actions=el('div',undefined,'user-actions');
    actions.append(actionButton('新增 KEY',()=>openEdit('token',user.id,'create')));
    owner.append(actions);return owner;
  }
  function render(){
    const columns=['用户','选择','KEY ID','KEY 名称','KEY','分组','KEY 状态','KEY 剩余额度（元）','KEY 已用额度（元）','到期时间（北京时间）','KEY 操作'];
    const head=el('tr');for(const text of columns)head.append(el('th',text));
    const pageKeys=visibleTokens();const checkbox=el('input');checkbox.type='checkbox';checkbox.setAttribute('aria-label','选择本页用户下的 KEY');checkbox.disabled=pageKeys.length===0;checkbox.checked=pageKeys.length>0&&(allFiltered||pageKeys.every(r=>selected.has(r.id)));
    checkbox.addEventListener('change',()=>{allFiltered=false;for(const row of pageKeys){if(checkbox.checked)selected.add(row.id);else selected.delete(row.id);}preview=null;render();selectionLabel();});head.children[1].replaceChildren(checkbox);
    $('management-head').replaceChildren(head);const fragment=document.createDocumentFragment();
    for(const user of rows){
      const keys=user.tokens.length?user.tokens:[null];
      keys.forEach((row,index)=>{
        const tr=el('tr',undefined,index===0?'user-group-start':'');tr.dataset.userId=user.id;
        if(index===0)userCell(tr,user,keys.length);
        if(!row){const empty=cell(tr,'暂无 KEY','empty-key');empty.colSpan=columns.length-1;fragment.append(tr);return;}
        const n=el('input');n.type='checkbox';n.checked=allFiltered||selected.has(row.id);n.setAttribute('aria-label',`选择 ${user.username} 的 KEY ${row.id}`);
        n.addEventListener('change',()=>{if(allFiltered){allFiltered=false;selected=new Set(visibleTokens().map(r=>r.id));}if(n.checked)selected.add(row.id);else selected.delete(row.id);preview=null;selectionLabel();});cell(tr,'').append(n);
        cell(tr,row.id);cell(tr,row.name);cell(tr,row.masked_key);cell(tr,'','user-group').append(keyGroupPicker(row,user));cell(tr,tokenStates[row.effective_status]||'未知状态',row.effective_status===1?'status-enabled':'status-disabled');
        cell(tr,row.unlimited_quota?'无限额度':money(row.remain_quota_yuan),'money');cell(tr,money(row.used_quota_yuan),'money');cell(tr,time(row.expired_time,true));
        const actions=cell(tr,'','row-actions');actions.append(actionButton('详情',()=>showTokenInfo(row)),actionButton('编辑',()=>openEdit('token',row.id,'edit')),actionButton('额度',()=>openEdit('token',row.id,'quota')),actionButton('查看 / 复制',()=>reveal(row.id)),actionButton(row.status===2?'启用':'禁用',()=>simpleAction('token',row.id,row.status===2?'enable':'disable')),actionButton('删除',()=>simpleAction('token',row.id,'delete'),true));
        fragment.append(tr);
      });
    }
    if(!rows.length){const tr=el('tr');const empty=cell(tr,'没有符合筛选条件的用户或 KEY。','empty-key');empty.colSpan=columns.length;fragment.append(tr);}
    $('management-rows').replaceChildren(fragment);
  }
  function showTokenInfo(row){$('token-info').replaceChildren();for(const [label,value] of [['KEY ID',row.id],['名称',row.name],['所属用户',row.username+'（ID '+row.user_id+'）'],['KEY 分组',groupName(row.token_group)],['创建时间',time(row.created_time,true)],['最近访问时间',time(row.accessed_time,true)],['到期时间',time(row.expired_time,true)],['模型限制',row.model_limits_enabled?row.model_limits||'空列表':'未启用'],['IP 限制',row.allow_ips||'不限制'],['跨组重试',row.cross_group_retry?'开启':'关闭']])$('token-info').append(el('dt',label),el('dd',value));$('token-info-dialog').showModal();}
  function groupPicker(id,groups){const wrap=$(id);wrap.replaceChildren();const search=el('input');search.type='search';search.placeholder='输入关键词筛选选项';search.setAttribute('aria-label','筛选分组选项');wrap.append(search);const labels=[];for(const group of groups){const label=el('label');const input=el('input');input.type='checkbox';input.value=group;label.append(input,el('span',groupName(group)));input.addEventListener('change',()=>{page=1;invalidate();query();});wrap.append(label);labels.push([label,groupName(group)]);}search.addEventListener('input',()=>{for(const [label,name] of labels)label.hidden=!keywordMatch(name,search.value);});}
  function selectOptions(node,values,value,includeInherited=true){node.replaceChildren();for(const name of [...(includeInherited?['','auto']:[]),...values.filter(v=>v!==''&&v!=='auto')]){const option=el('option',groupName(name));option.value=name;node.append(option);}if(value!==undefined){if(!Array.from(node.options).some(o=>o.value===value)){const option=el('option',value+'（当前配置）');option.value=value;node.append(option);}node.value=value;}}
  function field(name,label,value,type='text',full=false){const wrap=el('label',label,full?'full':'');const input=el(type==='textarea'?'textarea':type==='select'?'select':'input');input.name=name;if(type!=='textarea'&&type!=='select')input.type=type;if(type==='checkbox'){input.checked=Boolean(value);wrap.classList.add('check');}else input.value=value??'';if(type==='number'){input.step=name==='amount_yuan'?'any':'1';input.min=name==='amount_yuan'?'0':'1';}input.autocomplete='off';wrap.append(input);$('edit-fields').append(wrap);return input;}
  async function openEdit(kind,id,action){const data=action==='create'?{group:'',expired_time:-1,available_groups:window.managementGroups||[]}:action==='password'?{}:await api(id);edit={kind,id,action,data};$('edit-title').textContent=action==='create'?'新增 KEY':action==='quota'?'修改 KEY 剩余额度':'编辑 KEY';$('edit-fields').replaceChildren();$('edit-error').textContent='';if(action==='quota'){const mode=field('mode','操作','','select');for(const [v,label] of [['set','设置有限剩余额度'],['add','增加剩余额度（非原子）'],['subtract','减少剩余额度（非原子）'],['unlimited','设为无限额度']]){const o=el('option',label);o.value=v;mode.append(o);}field('amount_yuan','金额（元）',data.remain_quota_yuan||'0','number');}else{if(action==='create')field('user_id','所属用户 ID',id||userId||'','number');field('name','KEY 名称',data.name||'');const group=field('group','KEY 分组','','select');selectOptions(group,data.available_groups||[],data.group);const date=field('expired_time','到期时间（北京时间，留空永不过期）','','datetime-local');if(data.expired_time!==-1&&data.expired_time){const d=new Date((data.expired_time+8*3600)*1000);date.value=d.toISOString().slice(0,16);}field('model_limits_enabled','启用模型限制',data.model_limits_enabled,'checkbox');field('model_limits','允许模型（英文逗号分隔）',data.model_limits||'','textarea',true);field('allow_ips','允许 IP（每行一个，留空不限制）',data.allow_ips||'','textarea',true);field('cross_group_retry','自动分组跨组重试',data.cross_group_retry,'checkbox');field('auto_groups','自动分组范围（英文逗号分隔，留空跟随全局）',(data.auto_groups||[]).join(','),'text',true);if(action==='create'){field('unlimited_quota','无限额度',false,'checkbox');field('amount_yuan','有限剩余额度（元）','0','number');}}$('edit-warning').textContent=kind==='token'?'改组、编辑或额度调整前请暂停该 KEY 的并发调用。New API 为覆盖更新，不保证与并发消费原子执行；不会给用户账户充值。':'所有修改通过 New API 接口完成。密码不会保存到操作记录。';$('edit-dialog').showModal();}
  $('edit-form').addEventListener('submit',async event=>{event.preventDefault();if(!edit)return;const changes={};for(const n of $('edit-fields').querySelectorAll('[name]'))changes[n.name]=n.type==='checkbox'?n.checked:n.value;if(edit.kind==='token'&&edit.action!=='quota'){changes.expired_time=changes.expired_time?Math.floor(new Date(changes.expired_time+':00+08:00').getTime()/1000):-1;changes.auto_groups=changes.auto_groups.split(',').map(v=>v.trim()).filter(Boolean);}let id=edit.id;if(edit.kind==='token'&&edit.action==='create'){id=Number(changes.user_id);delete changes.user_id;}$('edit-save').disabled=true;try{await api(id+'/action',{action:edit.action,changes});$('edit-dialog').close();invalidate();await query();status('修改已提交，请以刷新后的实际数据为准。');}catch(e){$('edit-error').textContent=e.message;}finally{$('edit-save').disabled=false;}});
  $('key-group-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!pendingGroupChange||pendingGroupChange.submitted||groupSaving)return;
    const change=pendingGroupChange;change.submitted=true;groupSaving=true;$('key-group-save').disabled=true;$('key-group-cancel').disabled=true;
    try{
      await api(change.id+'/action',{action:'group',changes:groupChange(change.group)});
      $('key-group-dialog').close();invalidate();await query();status('KEY 分组修改已提交，请以刷新后的实际数据为准。');
    }catch(error){$('key-group-error').textContent=error.message+' 请关闭窗口并刷新核对；此窗口不会重复发送修改。';}
    finally{groupSaving=false;$('key-group-cancel').disabled=false;}
  });
  $('key-group-dialog').addEventListener('cancel',event=>{if(groupSaving)event.preventDefault();});
  $('key-group-dialog').addEventListener('close',()=>{const trigger=pendingGroupChange?.trigger;pendingGroupChange=null;$('key-group-error').textContent='';if(trigger?.isConnected)trigger.focus();});
  const closeKeyMenus=event=>{for(const picker of document.querySelectorAll('.key-group-picker[open]'))if(!event.target||!picker.contains(event.target))picker.open=false;};
  window.addEventListener('scroll',closeKeyMenus,true);window.addEventListener('resize',()=>closeKeyMenus({}));
  async function simpleAction(kind,id,action){if(!confirm(`确认${{enable:'启用',disable:'禁用',delete:'删除'}[action]} ${kind==='user'?'用户':'KEY'} ID ${id}？`))return;if(action==='delete'&&!confirm('删除可能不可恢复。用户删除会影响其关联 KEY，确认继续？'))return;await api(id+'/action',{action,changes:{}});invalidate();await query();}
  async function reveal(id){if(!confirm('查看完整 KEY 属于敏感操作，确认继续？'))return;const data=await api(id+'/action',{action:'reveal',changes:{}});$('revealed-key').value=data.result.key;$('revealed-key').type='password';$('reveal-dialog').showModal();}
  $('reveal-dialog').addEventListener('close',()=>{$('revealed-key').value='';$('revealed-key').type='password';});$('show-key').addEventListener('click',()=>{$('revealed-key').type=$('revealed-key').type==='password'?'text':'password';});$('copy-key').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('revealed-key').value);status('已复制 KEY，请注意保密。');}catch{status('浏览器不允许复制，请手动复制。',true);}});
  $('batch-preview').addEventListener('click',async()=>{try{preview=await api('groups/preview',{target_group:$('batch-target-group').value,all_filtered:allFiltered,filters:filters(),token_ids:Array.from(selected)});$('batch-summary').textContent=`将 ${preview.count} 个 KEY 改为 ${groupName($('batch-target-group').value)}。${preview.preview_truncated?'预览只展示前 100 个，确认后仍按完整冻结名单执行。':''}`;$('batch-rows').replaceChildren();for(const row of preview.rows){const tr=el('tr');for(const value of [row.id,row.username,groupName(row.before_group),groupName(row.after_group),'待执行'])cell(tr,value);tr.dataset.id=row.id;$('batch-rows').append(tr);}$('batch-progress').textContent='预览有效期 15 分钟；确认后才执行。';$('batch-apply').disabled=false;$('batch-dialog').showModal();}catch(e){status(e.message,true);}});
  $('batch-apply').addEventListener('click',async()=>{if(!preview||running)return;running=true;$('batch-apply').disabled=true;$('batch-cancel').disabled=true;let finished=0;const operation=preview.operation_id;try{while(true){const data=await api('operations/'+operation+'/apply',{});for(const result of data.results){const tr=$('batch-rows').querySelector(`[data-id="${result.id}"]`);if(tr)tr.lastChild.textContent=states[result.state]+(result.message?'：'+result.message:'');finished++;}$('batch-progress').textContent=`本次已收到 ${finished} 个结果。${data.done?'执行结束；失败或不明确项请核对操作记录。':''}`;if(data.done)break;}}catch(e){$('batch-progress').textContent=e.message;}finally{running=false;preview=null;$('batch-cancel').disabled=false;selected.clear();allFiltered=false;selectionLabel();await query();}});
  $('batch-dialog').addEventListener('cancel',event=>{if(running)event.preventDefault();else preview=null;});$('batch-cancel').addEventListener('click',()=>{if(!running){preview=null;$('batch-dialog').close();}});$('batch-target-group').addEventListener('change',()=>{preview=null;});
  for(const n of document.querySelectorAll('[data-close]'))n.addEventListener('click',()=>{if(n.dataset.close==='key-group-dialog'&&groupSaving)return;$(n.dataset.close).close();});for(const id of ['user-status-options','token-status-options'])$(id).addEventListener('change',()=>{page=1;invalidate();query();});$('management-search').addEventListener('submit',e=>{e.preventDefault();page=1;invalidate();query();});$('management-keyword').addEventListener('input',()=>invalidate());$('management-refresh').addEventListener('click',()=>{invalidate();query();});$('page-prev').addEventListener('click',()=>{page--;query();});$('page-next').addEventListener('click',()=>{page++;query();});$('select-filtered').addEventListener('click',()=>{allFiltered=true;selected.clear();preview=null;render();selectionLabel();});$('clear-token-selection').addEventListener('click',()=>{invalidate();render();});$('clear-user-filter').addEventListener('click',()=>{userId=null;$('clear-user-filter').hidden=true;page=1;invalidate();query();});$('new-key').addEventListener('click',()=>openEdit('token',userId||0,'create').catch(e=>status(e.message,true)));$('edit-dialog').addEventListener('close',()=>{$('edit-fields').replaceChildren();edit=null;});window.addEventListener('beforeunload',e=>{if(running||groupSaving){e.preventDefault();e.returnValue='';}});
  (async()=>{try{const raw=new URLSearchParams(root.location?.search||'').get('user_id');if(raw&&/^[1-9][0-9]*$/.test(raw)){userId=Number(raw);$('clear-user-filter').hidden=false;for(const n of $('user-status-options').querySelectorAll('input'))n.checked=true;}const data=await api('options');managementWritable=Boolean(data.persistence);window.managementGroups=data.groups;groupPicker('user-group-options',data.groups.filter(g=>g!==''&&g!=='auto'));groupPicker('token-group-options',['','auto',...data.groups.filter(g=>g!==''&&g!=='auto')]);selectOptions($('batch-target-group'),data.groups);if(!data.persistence)status('未配置监控数据库：只可查询，写操作需操作审计。',true);await query();}catch(e){status(e.message,true);}})();
})(typeof globalThis!=='undefined'?globalThis:this);
