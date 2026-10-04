const byId = id => document.getElementById(id);
let users = [];
let selected = new Set();
let selectedGroups = new Set();
let selectedStatuses = new Set(['enabled']);
let previewVersion = 0;
let previewLoading = false;
let pending = null;
let executing = false;
let resultCells = new Map();
const CONCURRENT_REQUESTS = 5;
const money = value => Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 6});
// Table cells only; preview messages, API payloads and source amounts retain precision.
const tableMoney = value => value === null || value === undefined ? '—' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
function status(message, error=false){byId('status').textContent=message;byId('status').classList.toggle('error',error);}
async function api(path, options={}){
  const response=await fetch(path,{cache:'no-store',...options});
  const body=await response.json();
  if(!response.ok)throw new Error(body.error||`请求失败（HTTP ${response.status}）`);
  return body;
}
const userStatus = user => user.status===1?'enabled':'disabled';
function eligibleUsers(){return users.filter(user=>selectedStatuses.has(userStatus(user)));}
function invalidatePreview(){
  if(executing)return;
  previewVersion++;previewLoading=false;pending=null;
  byId('confirm-dialog').close();
}
function pruneSelection(){
  const allowed=new Set(eligibleUsers().map(user=>user.id));
  selected=new Set([...selected].filter(id=>allowed.has(id)));
}
function visibleUsers(){
  const keyword=byId('search').value.trim().toLowerCase();
  return eligibleUsers().filter(user=>!keyword||String(user.id).includes(keyword)||user.username.toLowerCase().includes(keyword)||user.display_name.toLowerCase().includes(keyword));
}
function render(){
  byId('quota-settings').disabled=executing;byId('create-user').disabled=executing;byId('refresh-users').disabled=executing;
  const list=visibleUsers(), tbody=byId('users');tbody.replaceChildren();
  for(const user of list){
    const row=document.createElement('tr'),check=document.createElement('input');
    check.type='checkbox';check.checked=selected.has(user.id);check.setAttribute('aria-label',`选择 ${user.username}`);
    check.addEventListener('change',()=>{if(executing)return;invalidatePreview();if(check.checked)selected.add(user.id);else selected.delete(user.id);render();});
    const first=document.createElement('td');first.append(check);row.append(first);
    const id=document.createElement('td');id.textContent=user.id;row.append(id);
    const name=document.createElement('td');name.textContent=user.username;
    if(user.display_name){const display=document.createElement('span');display.className='display-name';display.textContent=user.display_name;name.append(display);}row.append(name);
    const group=document.createElement('td');group.className='user-group';group.textContent=user.user_group||'未分组';row.append(group);
    const state=document.createElement('td');state.className=user.status===1?'status-enabled':'status-disabled';state.textContent=user.status===1?'启用':'禁用';row.append(state);
    for(const value of [user.quota_yuan,user.used_quota_yuan]){const cell=document.createElement('td');cell.textContent=tableMoney(value);row.append(cell);}
    const keys=document.createElement('td'),link=document.createElement('a');link.href='/cockpit/keys/?user_id='+user.id;link.textContent=String(user.token_count??0);keys.append(link);row.append(keys);
    const remark=document.createElement('td');remark.textContent=user.remark||'';row.append(remark);
    const actions=document.createElement('td');actions.className='row-actions';globalThis.UserProfiles?.actions(user,actions,executing);row.append(actions);
    tbody.append(row);
  }
  byId('selected-count').textContent=selected.size;
  byId('select-all').checked=list.length>0&&list.every(user=>selected.has(user.id));
  byId('select-all').indeterminate=list.some(user=>selected.has(user.id))&&!byId('select-all').checked;
  byId('preview').disabled=selected.size===0||executing||previewLoading;
  byId('clear-selection').disabled=selected.size===0;
}
function renderGroups(){
  const counts=new Map();
  for(const user of eligibleUsers())counts.set(user.user_group,(counts.get(user.user_group)||0)+1);
  selectedGroups=new Set([...selectedGroups].filter(group=>counts.has(group)));
  const options=byId('group-options');options.replaceChildren();
  for(const group of [...counts.keys()].sort((a,b)=>a.localeCompare(b,'zh-CN'))){
    const label=document.createElement('label'),check=document.createElement('input');
    check.type='checkbox';check.checked=selectedGroups.has(group);
    check.addEventListener('change',()=>{
      if(check.checked)selectedGroups.add(group);else selectedGroups.delete(group);
      updateGroupControls();
    });
    label.append(check,document.createTextNode(`${group||'未分组'}（${counts.get(group)} 人）`));
    options.append(label);
  }
  updateGroupControls();
}
function updateGroupControls(){
  byId('group-summary').textContent=selectedGroups.size?`已选 ${selectedGroups.size} 个用户组`:'选择用户组';
  byId('select-groups').disabled=selectedGroups.size===0;
  byId('deselect-groups').disabled=selectedGroups.size===0;
}
async function load(){
  status('正在读取用户…');
  try{invalidatePreview();users=(await api('/cockpit/users/api/users')).rows;invalidatePreview();pruneSelection();renderGroups();render();status(`当前状态范围 ${eligibleUsers().length} 人，共 ${users.length} 个用户。`);}
  catch(error){status(error.message,true);}
}
byId('search').addEventListener('input',render);
function changeStatusFilter(){
  if(executing)return;
  selectedStatuses=new Set();
  if(byId('status-enabled').checked)selectedStatuses.add('enabled');
  if(byId('status-disabled').checked)selectedStatuses.add('disabled');
  invalidatePreview();
  byId('status-summary').textContent=selectedStatuses.size===2?'启用、禁用用户':selectedStatuses.has('enabled')?'启用用户':selectedStatuses.has('disabled')?'禁用用户':'未选择状态';
  pruneSelection();renderGroups();render();
  status(`当前状态范围 ${eligibleUsers().length} 人，已选 ${selected.size} 人。`);
}
byId('status-enabled').checked=true;
byId('status-disabled').checked=false;
byId('status-enabled').addEventListener('change',changeStatusFilter);
byId('status-disabled').addEventListener('change',changeStatusFilter);
byId('select-all').addEventListener('change',event=>{
  if(executing)return;invalidatePreview();
  for(const user of visibleUsers()){if(event.target.checked)selected.add(user.id);else selected.delete(user.id);}render();
});
byId('select-groups').addEventListener('click',()=>{
  if(executing)return;invalidatePreview();
  for(const user of eligibleUsers())if(selectedGroups.has(user.user_group))selected.add(user.id);
  render();status(`已选 ${selected.size} 人，请核对名单。`);
});
byId('deselect-groups').addEventListener('click',()=>{
  if(executing)return;invalidatePreview();
  for(const user of eligibleUsers())if(selectedGroups.has(user.user_group))selected.delete(user.id);
  render();status(`已选 ${selected.size} 人。`);
});
byId('clear-selection').addEventListener('click',()=>{if(executing)return;invalidatePreview();selected.clear();render();status('已清空所选用户。');});
byId('preview').addEventListener('click',async()=>{
  if(executing||previewLoading||selected.size===0)return;
  const amount=byId('amount').value.trim();
  if(!amount||!byId('amount').checkValidity()){status('请输入有效的每人金额。',true);return;}
  const body={user_ids:[...selected].sort((a,b)=>a-b),mode:byId('mode').value,amount_yuan:amount};
  const version=++previewVersion;previewLoading=true;
  byId('preview').disabled=true;
  try{
    const result=await api('/cockpit/users/api/preview',{method:'POST',headers:{'Content-Type':'application/json','X-Quota-Action':'preview'},body:JSON.stringify(body)});
    if(version!==previewVersion)return;
    pending=body;byId('confirm-summary').textContent=`将为 ${result.users.length} 人每人${result.mode==='add'?'增加':'减少'} ¥${money(result.amount_yuan)}。`;
    const rows=byId('preview-rows');rows.replaceChildren();resultCells=new Map();
    for(const user of result.users){const tr=document.createElement('tr');for(const value of [user.username,tableMoney(user.before_yuan),tableMoney(user.estimated_after_yuan)]){const td=document.createElement('td');td.textContent=value;tr.append(td);}const state=document.createElement('td');state.textContent='待执行';tr.append(state);resultCells.set(user.id,state);rows.append(tr);}
    byId('execution-progress').textContent='确认后每组最多 5 人并发，当前组全部返回后再发下一组。';
    byId('apply').disabled=false;byId('cancel').disabled=false;byId('cancel').textContent='取消';
    byId('confirm-dialog').showModal();status('请核对预览，再确认执行。');
  }catch(error){if(version===previewVersion){pending=null;status(error.message,true);}}
  finally{if(version===previewVersion){previewLoading=false;render();}}
});
for(const [id,event] of [['amount','input'],['mode','change']])byId(id).addEventListener(event,()=>{if(executing)return;invalidatePreview();render();});
byId('cancel').addEventListener('click',()=>{if(executing)return;invalidatePreview();render();});
byId('confirm-dialog').addEventListener('cancel',event=>{if(executing)event.preventDefault();else pending=null;});
globalThis.addEventListener?.('beforeunload',event=>{if(executing){event.preventDefault();event.returnValue='';}});
function setResult(userId,message){const cell=resultCells.get(userId);if(cell)cell.textContent=message;}
byId('apply').addEventListener('click',async()=>{
  if(!pending||executing)return;
  const body=pending;pending=null;executing=true;byId('apply').disabled=true;byId('cancel').disabled=true;render();
  let succeeded=0,attempted=0,failedMessage='';
  try{
    for(let offset=0;offset<body.user_ids.length;offset+=CONCURRENT_REQUESTS){
      const wave=body.user_ids.slice(offset,offset+CONCURRENT_REQUESTS);
      attempted+=wave.length;
      for(const id of wave)setResult(id,'处理中');
      byId('execution-progress').textContent=`正在处理第 ${offset+1}～${offset+wave.length} 人 / 共 ${body.user_ids.length} 人；已确认成功 ${succeeded} 人。请勿刷新或关闭页面。`;
      let result;
      try{
        result=await api('/cockpit/users/api/apply',{method:'POST',headers:{'Content-Type':'application/json','X-Quota-Action':'confirm'},body:JSON.stringify({...body,user_ids:wave})});
        const rows=result.results;
        if(!Array.isArray(rows)||rows.length!==wave.length||new Set(rows.map(row=>row.id)).size!==wave.length||rows.some(row=>!wave.includes(row.id)||typeof row.ok!=='boolean'))throw new Error('接口返回的执行结果不完整');
      }catch(error){
        for(const id of wave)setResult(id,'结果待核对：'+error.message);
        failedMessage=`${error.message}；本组 ${wave.length} 人的结果需核对`;
        break;
      }
      for(const row of result.results){if(row.ok)succeeded++;setResult(row.id,row.ok?'成功':`失败 / 待核对：${row.message||'未知错误'}`);}
      if(!result.completed||result.results.some(row=>!row.ok)){
        failedMessage=result.results.find(row=>!row.ok)?.message||'本组未全部完成';break;
      }
    }
  }catch(error){failedMessage=`${error.message}；请核对正在处理的用户`;
  }finally{
    for(const id of body.user_ids.slice(attempted))setResult(id,'未发起');
    const remaining=body.user_ids.length-attempted;
    const message=failedMessage?`已确认成功 ${succeeded} 人，后续 ${remaining} 人未发起；${failedMessage}。不要直接重试，请先核对结果。`:`完成：${succeeded} 人已${body.mode==='add'?'增加':'减少'} ¥${money(body.amount_yuan)}。`;
    byId('execution-progress').textContent=message;status(message,!!failedMessage);
    executing=false;selected.clear();byId('cancel').disabled=false;byId('cancel').textContent='关闭';
    // Keep per-user results visible; the consumed confirmation cannot be replayed.
    await load();status(message,!!failedMessage);
  }
});
globalThis.quotaIsExecuting=()=>executing;
globalThis.quotaReloadUsers=load;
globalThis.quotaUserMessage=status;
byId('create-user').addEventListener('click',()=>{if(!executing)globalThis.UserProfiles?.open(0,'create');});
byId('refresh-users').addEventListener('click',()=>{if(!executing)load();});
load();
