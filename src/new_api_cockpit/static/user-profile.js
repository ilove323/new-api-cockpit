/* User-only CRUD editor. Quota deltas remain in quota.js, never profile PUTs. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  let edit=null,busy=false,needsReview=false,epoch=0,groupSaving=false,pendingGroupChange=null;
  const blocked=()=>busy||groupSaving||globalThis.quotaIsExecuting?.();
  const groupName=group=>group||'未分组';
  async function api(path,body){
    const options={cache:'no-store'};
    if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json','X-Management-Action':'confirm'},body:JSON.stringify(body)});
    let response,data;
    try{response=await fetch('/cockpit/api/users/'+path,options);data=await response.json();}
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
      const options=await fetch('/cockpit/api/keys/options',{cache:'no-store'});
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
  function groupPicker(user,disabled){
    const picker=node('details'),trigger=node('summary',groupName(user.user_group)),menu=node('div');
    picker.className='user-group-picker';trigger.className='user-group-trigger';menu.className='user-group-menu';
    trigger.title='点击切换用户组';trigger.setAttribute('aria-label',`切换用户 ${user.username}（ID ${user.id}）的用户组（${groupName(user.user_group)}）`);
    trigger.setAttribute('aria-disabled',String(Boolean(disabled)));
    menu.setAttribute('role','group');menu.setAttribute('aria-label','选择目标用户组');picker.append(trigger,menu);
    let version=0;
    trigger.addEventListener('click',event=>{if(disabled||blocked())event.preventDefault();});
    function position(){
      const rect=trigger.getBoundingClientRect(),width=Math.min(280,window.innerWidth-16);
      const below=window.innerHeight-rect.bottom-8,above=rect.top-8,up=below<160&&above>below;
      menu.style.width=width+'px';menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';
      menu.style.top=up?'auto':Math.max(8,rect.bottom+6)+'px';menu.style.bottom=up?Math.max(8,window.innerHeight-rect.top+6)+'px':'auto';
      menu.style.maxHeight=Math.max(0,Math.min(320,(up?above:below)-6))+'px';
    }
    picker.addEventListener('toggle',async()=>{
      const ticket=++version;if(!picker.open)return;
      if(disabled||blocked()){picker.open=false;return;}
      menu.replaceChildren(node('p','正在读取可用用户组…'));position();
      try{
        const data=await api(user.id+'/groups');
        if(ticket!==version||!picker.open||!picker.isConnected)return;
        if(!data.persistence){const error=node('p','未配置监控数据库，只可查询，不能保存用户组修改。');error.className='error';menu.replaceChildren(error);return;}
        menu.replaceChildren(node('p','当前用户组：'+groupName(data.group)));
        const search=node('input');search.type='search';search.autocomplete='off';search.placeholder='输入关键词筛选分组';search.setAttribute('aria-label','筛选目标用户组');menu.append(search);
        const options=[];
        for(const group of data.available_groups){
          const button=node('button',group);button.className='user-group-option';button.type='button';button.dataset.group=group;button.disabled=group===data.group;
          if(button.disabled){button.classList.add('is-current');button.title='当前用户组';}
          button.addEventListener('click',()=>{
            if(disabled||blocked()||group===data.group)return;
            picker.open=false;pendingGroupChange={id:user.id,group,trigger,submitted:false};
            $('user-group-summary').textContent=`用户 ${data.username}（ID ${user.id}）\n${groupName(data.group)} → ${groupName(group)}`;
            $('user-group-error').textContent='';$('user-group-save').disabled=false;$('user-group-cancel').disabled=false;
            $('user-group-dialog').showModal();$('user-group-cancel').focus();
          });
          menu.append(button);options.push(button);
        }
        const empty=node('p',options.length?'没有匹配的用户组。':'New API 未配置可用用户组。');empty.className='hint';empty.hidden=options.length>0;menu.append(empty);
        search.addEventListener('input',()=>{for(const button of options)button.hidden=!button.textContent.toLowerCase().includes(search.value.toLowerCase());empty.hidden=options.some(button=>!button.hidden);});
        search.focus();
      }catch(error){if(ticket===version&&picker.open&&picker.isConnected){const message=node('p',error.message);message.className='error';menu.replaceChildren(message);}}
    });
    return picker;
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
  $('user-group-form').addEventListener('submit',async event=>{
    event.preventDefault();if(blocked()||!pendingGroupChange||pendingGroupChange.submitted)return;
    const change=pendingGroupChange;change.submitted=true;groupSaving=true;$('user-group-save').disabled=true;$('user-group-cancel').disabled=true;
    try{
      await api(change.id+'/action',{action:'group',changes:{group:change.group}});
      $('user-group-dialog').close();await globalThis.quotaReloadUsers?.();
      globalThis.quotaUserMessage?.('用户组修改已提交，请以刷新后的实际数据为准。');
    }catch(error){$('user-group-error').textContent=error.message+' 请关闭窗口并刷新核对；此窗口不会重复发送修改。';}
    finally{groupSaving=false;$('user-group-cancel').disabled=false;}
  });
  $('user-group-cancel').addEventListener('click',()=>{if(!groupSaving)$('user-group-dialog').close();});
  $('user-group-dialog').addEventListener('cancel',event=>{if(groupSaving)event.preventDefault();});
  $('user-group-dialog').addEventListener('close',()=>{const trigger=pendingGroupChange?.trigger;pendingGroupChange=null;$('user-group-error').textContent='';if(trigger?.isConnected)trigger.focus();});
  const closeGroupMenus=event=>{for(const picker of document.querySelectorAll('.user-group-picker[open]'))if(!event.target||!picker.contains(event.target))picker.open=false;};
  window.addEventListener('scroll',closeGroupMenus,true);window.addEventListener('resize',()=>closeGroupMenus({}));
  window.addEventListener('beforeunload',event=>{if(groupSaving){event.preventDefault();event.returnValue='';}});
  globalThis.UserProfiles={open,actions,groupPicker};
})();
