const byId = id => document.getElementById(id);
let users = [];
let selected = new Set();
let selectedGroups = new Set();
let pending = null;
const money = value => Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 6});
function status(message, error=false){byId('status').textContent=message;byId('status').classList.toggle('error',error);}
async function api(path, options={}){
  const response=await fetch(path,{cache:'no-store',...options});
  const body=await response.json();
  if(!response.ok)throw new Error(body.error||`请求失败（HTTP ${response.status}）`);
  return body;
}
function visibleUsers(){
  const keyword=byId('search').value.trim().toLowerCase();
  return users.filter(user=>!keyword||String(user.id).includes(keyword)||user.username.toLowerCase().includes(keyword)||user.display_name.toLowerCase().includes(keyword));
}
function render(){
  const list=visibleUsers(), tbody=byId('users');tbody.replaceChildren();
  for(const user of list){
    const row=document.createElement('tr'),check=document.createElement('input');
    check.type='checkbox';check.checked=selected.has(user.id);check.setAttribute('aria-label',`选择 ${user.username}`);
    check.addEventListener('change',()=>{if(check.checked)selected.add(user.id);else selected.delete(user.id);render();});
    const first=document.createElement('td');first.append(check);row.append(first);
    const id=document.createElement('td');id.textContent=user.id;row.append(id);
    const name=document.createElement('td');name.textContent=user.username;
    if(user.display_name){const display=document.createElement('span');display.className='display-name';display.textContent=user.display_name;name.append(display);}row.append(name);
    const group=document.createElement('td');group.textContent=user.user_group||'未分组';row.append(group);
    const state=document.createElement('td');state.textContent=user.status===1?'启用':'禁用';row.append(state);
    for(const value of [user.quota_yuan,user.used_quota_yuan]){const cell=document.createElement('td');cell.textContent=money(value);row.append(cell);}
    tbody.append(row);
  }
  byId('selected-count').textContent=selected.size;
  byId('select-all').checked=list.length>0&&list.every(user=>selected.has(user.id));
  byId('select-all').indeterminate=list.some(user=>selected.has(user.id))&&!byId('select-all').checked;
  byId('preview').disabled=selected.size===0||selected.size>100;
  byId('clear-selection').disabled=selected.size===0;
}
function renderGroups(){
  const counts=new Map();
  for(const user of users)counts.set(user.user_group,(counts.get(user.user_group)||0)+1);
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
  try{users=(await api('/quota/api/users')).rows;selected=new Set([...selected].filter(id=>users.some(user=>user.id===id)));renderGroups();render();status(`共 ${users.length} 个用户。`);}
  catch(error){status(error.message,true);}
}
byId('search').addEventListener('input',render);
byId('select-all').addEventListener('change',event=>{
  for(const user of visibleUsers()){if(event.target.checked)selected.add(user.id);else selected.delete(user.id);}render();
});
byId('select-groups').addEventListener('click',()=>{
  for(const user of users)if(selectedGroups.has(user.user_group))selected.add(user.id);
  render();status(selected.size>100?'已选超过 100 人；单次最多操作 100 人，请缩小选择范围。':`已选 ${selected.size} 人，请核对名单。`,selected.size>100);
});
byId('deselect-groups').addEventListener('click',()=>{
  for(const user of users)if(selectedGroups.has(user.user_group))selected.delete(user.id);
  render();status(`已选 ${selected.size} 人。`);
});
byId('clear-selection').addEventListener('click',()=>{selected.clear();render();status('已清空所选用户。');});
byId('preview').addEventListener('click',async()=>{
  const amount=byId('amount').value.trim();
  if(!amount||!byId('amount').checkValidity()){status('请输入有效的每人金额。',true);return;}
  const body={user_ids:[...selected].sort((a,b)=>a-b),mode:byId('mode').value,amount_yuan:amount};
  byId('preview').disabled=true;
  try{
    const result=await api('/quota/api/preview',{method:'POST',headers:{'Content-Type':'application/json','X-Quota-Action':'preview'},body:JSON.stringify(body)});
    pending=body;byId('confirm-summary').textContent=`将为 ${result.users.length} 人每人${result.mode==='add'?'增加':'减少'} ¥${money(result.amount_yuan)}。`;
    const rows=byId('preview-rows');rows.replaceChildren();
    for(const user of result.users){const tr=document.createElement('tr');for(const value of [user.username,money(user.before_yuan),money(user.estimated_after_yuan)]){const td=document.createElement('td');td.textContent=value;tr.append(td);}rows.append(tr);}
    byId('confirm-dialog').showModal();status('请核对预览，再确认执行。');
  }catch(error){pending=null;status(error.message,true);}
  finally{byId('preview').disabled=false;}
});
byId('cancel').addEventListener('click',()=>{pending=null;byId('confirm-dialog').close();});
byId('apply').addEventListener('click',async()=>{
  if(!pending)return;
  const body=pending;pending=null;byId('apply').disabled=true;byId('cancel').disabled=true;
  try{
    const result=await api('/quota/api/apply',{method:'POST',headers:{'Content-Type':'application/json','X-Quota-Action':'confirm'},body:JSON.stringify(body)});
    byId('confirm-dialog').close();selected.clear();
    const succeeded=result.results.filter(row=>row.ok).length;
    if(result.completed)status(`完成：${succeeded} 人已${result.mode==='add'?'增加':'减少'} ¥${money(result.amount_yuan)}。`);
    else status(`仅完成 ${succeeded} 人；${result.results.find(row=>!row.ok)?.message||'操作未全部完成'}。不要直接重试，请先核对。`,true);
    const message=byId('status').textContent;const failed=byId('status').classList.contains('error');
    await load();status(message,failed);
  }catch(error){byId('confirm-dialog').close();status(`${error.message} 请勿直接重试，先核对实际额度。`,true);}
  finally{byId('apply').disabled=false;byId('cancel').disabled=false;}
});
load();
