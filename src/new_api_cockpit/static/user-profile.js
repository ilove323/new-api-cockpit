/* User-only CRUD editor. Quota deltas remain in quota.js, never profile PUTs. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  let edit=null,busy=false,needsReview=false,epoch=0;
  const blocked=()=>busy||globalThis.quotaIsExecuting?.();
  async function api(path,body){
    const options={cache:'no-store'};
    if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json','X-Management-Action':'confirm'},body:JSON.stringify(body)});
    let response,data;
    try{response=await fetch('/cockpit/users/api/user/'+path,options);data=await response.json();}
    catch{const e=new Error('请求中断，结果需核对。请刷新用户及操作记录，不要重复提交。');e.uncertain=body!==undefined;throw e;}
    if(!response.ok){const e=new Error(data.error||'用户操作失败。');e.uncertain=body!==undefined&&response.status>=500;throw e;}
    return data;
  }
  function field(name,label,value='',type='text'){
    const wrap=node('label',label),input=node(type==='textarea'?'textarea':type==='select'?'select':'input');
    input.name=name;input.value=value??'';input.autocomplete='off';
    if(type!=='textarea'&&type!=='select')input.type=type;
    if(type==='password'){input.required=true;input.minLength=8;}
    wrap.append(input);$('user-profile-fields').append(wrap);return input;
  }
  function clear(){$('user-profile-fields').replaceChildren();edit=null;needsReview=false;epoch++;}
  function controls(){$('user-profile-save').disabled=busy||needsReview;$('user-profile-cancel').disabled=busy;}
  async function open(id,action='edit'){
    if(blocked())return;
    const ticket=++epoch;busy=true;controls();
    try{
      const options=await fetch('/cockpit/keys/api/options',{cache:'no-store'});
      const config=await options.json();if(!options.ok)throw new Error(config.error||'无法读取管理选项。');
      const data=action==='create'||action==='password'?{}:await api(id);
      if(ticket!==epoch)return;
      edit={id,action};needsReview=false;$('user-profile-error').textContent='';$('user-profile-fields').replaceChildren();
      $('user-profile-title').textContent=action==='create'?'新增用户':action==='password'?'重置用户密码':'编辑用户资料';
      if(action==='password'){field('password','新密码','','password');field('password_confirm','确认新密码','','password');}
      else{
        field('username','用户名',data.username);field('display_name','显示名',data.display_name);
        if(action==='create')field('password','初始密码','','password');
        else{
          const groups=field('group','用户组','','select');
          for(const group of [...new Set([...(config.groups||[]).filter(g=>g!=='auto'),data.group??''])]){const o=node('option',group||'未分组');o.value=group;groups.append(o);}
          groups.value=data.group??'';field('remark','备注',data.remark,'textarea');
        }
      }
      $('user-profile-dialog').showModal();
    }catch(e){globalThis.quotaUserMessage?.(e.message,true);}
    finally{busy=false;controls();}
  }
  async function simple(id,action){
    if(blocked()||!confirm(`确认${{enable:'启用',disable:'禁用',delete:'删除'}[action]}用户 ID ${id}？`))return;
    if(action==='delete'&&!confirm('用户删除会影响关联 KEY，且不可自动恢复。确认删除？'))return;
    busy=true;
    try{await api(id+'/action',{action,changes:{}});await globalThis.quotaReloadUsers?.();}
    catch(e){globalThis.quotaUserMessage?.(e.message,true);}
    finally{busy=false;}
  }
  function actions(user,parent,disabled){
    for(const [label,fn,danger,icon] of [['编辑',()=>open(user.id),false,'pencil'],['重置密码',()=>open(user.id,'password'),false,'key-round'],[user.status===1?'禁用':'启用',()=>simple(user.id,user.status===1?'disable':'enable'),user.status===1,user.status===1?'power-off':'power'],['删除',()=>simple(user.id,'delete'),true,'trash-2']]){
      const button=node('button',label);button.type='button';button.disabled=disabled;button.className='action-icon'+(danger?' danger':'');
      button.setAttribute('data-icon',icon);button.setAttribute('aria-label',label+'用户 '+user.username);button.title=label;
      button.addEventListener('click',fn);parent.append(button);
    }
  }
  $('user-profile-form').addEventListener('submit',async event=>{
    event.preventDefault();if(blocked()||!edit||needsReview)return;
    const changes={};for(const n of $('user-profile-fields').querySelectorAll('[name]'))changes[n.name]=n.value;
    if(edit.action==='password'&&changes.password!==changes.password_confirm){$('user-profile-error').textContent='两次密码不一致。';return;}
    const current=edit;busy=true;controls();
    try{await api(current.id+'/action',{action:current.action,changes});busy=false;$('user-profile-dialog').close();await globalThis.quotaReloadUsers?.();}
    catch(e){needsReview=!!e.uncertain;$('user-profile-error').textContent=e.message;}
    finally{for(const name of ['password','password_confirm'])delete changes[name];busy=false;controls();}
  });
  $('user-profile-cancel').addEventListener('click',()=>{if(!busy)$('user-profile-dialog').close();});
  $('user-profile-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  $('user-profile-dialog').addEventListener('close',clear);
  globalThis.UserProfiles={open,actions};
})();
