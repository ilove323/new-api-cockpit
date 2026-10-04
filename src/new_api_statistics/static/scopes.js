/* Shared ledger identity. Global notification requests deliberately bypass scoping. */
const Scope = (() => {
  let current=null, epoch=0, controller=new AbortController();
  const stale=()=>new DOMException('账本已切换','AbortError');
  const name=row=>row.kind==='all'?'全部':row.kind==='ungrouped'?'未分组':row.tag_value;
  function url(path){
    if(!current)throw new Error('请先选择账本');
    const target=new URL(path,location.origin);target.searchParams.set('scope_id',current.id);
    return target.pathname+target.search;
  }
  function guard(ticket){if(ticket!==epoch)throw stale();}
  async function request(path,options={}){
    const ticket=epoch;
    const response=await fetch(url(path),{...options,signal:controller.signal});guard(ticket);
    return {ok:response.ok,status:response.status,json:async()=>{const data=await response.json();guard(ticket);return data;}};
  }
  function select(row){
    if(current&&String(current.id)===String(row.id))return;
    controller.abort();controller=new AbortController();epoch++;current=row;
    const target=new URL(location.href);target.searchParams.set('scope_id',row.id);history.replaceState(null,'',target);
    document.querySelectorAll('#scope-tabs button').forEach(b=>{const active=b.dataset.scopeId===String(row.id);b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
    document.getElementById('scope-status').textContent='当前账本：'+name(row);
    window.dispatchEvent(new Event('scopechange'));
  }
  async function init(){
    try{
      const response=await fetch('/cockpit/statistics/api/scopes');if(!response.ok)throw new Error('账本列表读取失败，请刷新重试');
      const data=await response.json();const rows=data.rows;
      if(!Array.isArray(rows)||!rows.length)throw new Error('暂无可用账本');
      const order={all:0,tag:1,ungrouped:2};rows.sort((a,b)=>order[a.kind]-order[b.kind]);
      const nav=document.getElementById('scope-tabs');nav.replaceChildren();
      rows.forEach(row=>{const b=document.createElement('button');b.type='button';b.setAttribute('role','tab');b.dataset.scopeId=String(row.id);b.textContent=name(row);b.addEventListener('click',()=>select(row));
        b.addEventListener('keydown',e=>{const buttons=[...nav.children],i=buttons.indexOf(b);const index=e.key==='ArrowRight'?(i+1)%buttons.length:e.key==='ArrowLeft'?(i+buttons.length-1)%buttons.length:e.key==='Home'?0:e.key==='End'?buttons.length-1:null;if(index!==null){e.preventDefault();buttons[index].focus();buttons[index].click();}});nav.append(b);});
      const requested=new URLSearchParams(location.search).get('scope_id');
      select(rows.find(r=>String(r.id)===requested)||rows.find(r=>r.kind==='all')||rows[0]);
    }catch(e){document.getElementById('scope-status').textContent=e.message;}
  }
  return {get current(){return current;},get epoch(){return epoch;},name,url,request,guard,init};
})();
