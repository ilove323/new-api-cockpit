/* Fetch one database aggregate; user and model filters only affect the detail table. */
const $ = id => document.getElementById(id);
let snapshot = null;
let tokenSnapshot = null;
let tokenOptions = [];
let groupOptions = [];
let filteredSelectionSnapshot = null;
let detailMode = 'summary';
let modelMode = 'model';
let ranking = 'model_amount';
const tokenFields = ['total_tokens','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'];
const detailColumns = ['username','request_count','model_name','tier_name',...tokenFields,'group_ratio','input_price','output_price','cache_price','write_price','amount'];
const modelSummaryColumns = ['group_ratio','input_price','output_price','cache_price','write_price'];
const columnStorageKey = 'new-api-statistics.visible-columns.v2';
let modelColumnSelection = null;
const developerLevel = Number(new URLSearchParams(window.location.search).get('dev')||0);
const developerMode = developerLevel>=1;
const failureMode = developerLevel>=2;
const number = (n, digits=6) => n === null || n === undefined ? '—' : Number(n).toLocaleString('zh-CN',{maximumFractionDigits:digits});
// Presentation only: never round the source values used by calculations or tooltips.
const tableMoney = n => n === null || n === undefined ? '—' : Number(n).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
function priceDisplay(row,key){
  return !(row.pricing_buckets||row.has_pricing_buckets)&&row.pricing_mode==='expression'&&!(row.price_tiers?.length||row.price_tier_count)?'无法拆分':tableMoney(row[key]);
}
const cell = (tr, value, cls='', column='') => {const td=document.createElement('td');td.textContent=value;td.className=cls;if(column)td.dataset.column=column;tr.append(td);return td;};
const userCell = (tr,row) => {
  const td=cell(tr,row.username,'username','username');
  if(row.display_name){const name=document.createElement('span');name.className='display-name';name.textContent=row.display_name;td.append(name);}
  return td;
};
function aggregateModels(data){
  const grouped=new Map(),priceFields=['group_ratio','input_price','output_price','cache_price','write_price'];
  for(const row of data){
    const key=[row.user_id,row.username,detailMode==='token'?row.token_id:''].join('\u0000');
    if(!grouped.has(key)){
      grouped.set(key,{...row,model_name:'',amount:0,request_count:0,failure_count:0,failure_codes:{},...Object.fromEntries(tokenFields.map(field=>[field,0])),...Object.fromEntries(priceFields.map(field=>[field,null])),_sourceRows:[]});
    }
    const total=grouped.get(key);
    total.amount+=Number(row.amount);total.request_count+=Number(row.request_count);
    tokenFields.forEach(field=>total[field]=total[field]===null||row[field]===null?null:total[field]+Number(row[field]));
    for(const [code,count] of Object.entries(row.failure_codes||{}))total.failure_codes[code]=(total.failure_codes[code]||0)+Number(count);
    total.failure_count+=Number(row.failure_count||0);
    total._sourceRows.push(row);
  }
  return [...grouped.values()];
}
function selectedRows(){
  if(!snapshot)return [];
  const users=selectedFilterValues('user'),models=selectedFilterValues('model');
  const selected=selectedFilterValues('token').size||selectedFilterValues('group').size;
  const rows=selected?(filteredSelectionSnapshot?.rows||[]):detailMode==='token'?(tokenSnapshot?.rows||[]):snapshot.rows;
  const filtered=rows.filter(r=>(!users.size||users.has(r.username))&&(!models.size||models.has(r.model_name)));
  return modelMode==='summary'?aggregateModels(filtered):filtered;
}
function visibleColumns(){
  return new Set([...document.querySelectorAll('[data-column-toggle]:checked')].map(input=>input.dataset.columnToggle));
}
function saveVisibleColumns(){
  const visible=visibleColumns();
  if(modelMode==='summary'&&modelColumnSelection)modelColumnSelection.forEach(column=>visible.add(column));
  try{localStorage.setItem(columnStorageKey,JSON.stringify([...visible]));}catch{}
}
function applyColumnVisibility(){
  const visible=visibleColumns();
  const hiddenByMode=new Set(modelMode==='summary'?['model_name','tier_name',...modelSummaryColumns]:[]);
  document.querySelectorAll('#usage-table [data-column]').forEach(element=>{
    if(detailColumns.includes(element.dataset.column))element.hidden=!visible.has(element.dataset.column)||hiddenByMode.has(element.dataset.column);
  });
  document.querySelectorAll('#usage-table [data-token-column]').forEach(element=>element.hidden=detailMode!=='token');
  const columns=[...visible].filter(column=>!hiddenByMode.has(column)).length+(detailMode==='token'?1:0)+(developerMode?2:0)+(failureMode?1:0);
  $('usage-table').style.setProperty('--usage-table-min-width',Math.max(480,columns*115)+'px');
}
function loadVisibleColumns(){
  let saved;
  try{
    saved=JSON.parse(localStorage.getItem(columnStorageKey));
    if(!Array.isArray(saved)){
      saved=JSON.parse(localStorage.getItem('new-api-statistics.visible-columns'));
      if(Array.isArray(saved))saved=[...saved,'tier_name'];
    }
  }catch{}
  if(!Array.isArray(saved)||!saved.length)return;
  document.querySelectorAll('[data-column-toggle]').forEach(input=>input.checked=saved.includes(input.dataset.columnToggle));
}
function selectedFilterValues(id){
  return new Set([...document.querySelectorAll(`#${id}-options input:checked`)].map(input=>input.value));
}
function updateFilterSummary(id,placeholder){
  const selected=[...document.querySelectorAll(`#${id}-options input:checked`)];
  const summary=$(`${id}-summary`);
  summary.textContent=!selected.length?placeholder:selected.length===1?selected[0].dataset.label:`已选 ${selected.length} 项`;
  summary.title=selected.map(input=>input.dataset.label).join('、');
}
function populateFilter(id,placeholder,values,label,change=()=>{if(snapshot)renderDetails();}){
  const options=$(`${id}-options`),selected=selectedFilterValues(id);
  options.replaceChildren();
  const search=document.createElement('input');
  search.type='search';search.className='filter-search';search.placeholder='输入关键字筛选';search.autocomplete='off';search.setAttribute('aria-label',`筛选${placeholder.replace('全部','')}`);
  options.append(search);
  values.forEach(value=>{
    const text=label(value),row=document.createElement('label'),input=document.createElement('input');
    input.type='checkbox';input.value=value;input.dataset.label=text;input.checked=selected.has(value);
    input.addEventListener('change',()=>{updateFilterSummary(id,placeholder);change();});
    row.append(input,document.createTextNode(text));options.append(row);
  });
  const empty=document.createElement('p');empty.className='filter-empty';empty.textContent='无匹配选项';empty.hidden=true;options.append(empty);
  const all=document.createElement('button');all.type='button';all.textContent='全部';
  all.addEventListener('click',()=>{
    options.querySelectorAll('input:checked').forEach(input=>input.checked=false);
    updateFilterSummary(id,placeholder);change();
  });
  options.append(all);
  search.addEventListener('input',()=>{
    const keyword=search.value.trim().toLocaleLowerCase('zh-CN');let matches=0;
    options.querySelectorAll('label').forEach(row=>{
      const matched=!keyword||row.textContent.toLocaleLowerCase('zh-CN').includes(keyword);
      row.hidden=!matched;
      row.style.display=matched?'':'none';
      if(matched)matches++;
    });
    empty.hidden=matches!==0;
    empty.style.display=matches?'none':'';
  });
  updateFilterSummary(id,placeholder);
}
const tokenFilterValue = row => String(row.token_id);
function populateTokenFilter(){
  const counts=new Map();
  tokenOptions.forEach(row=>counts.set(row.token_name,(counts.get(row.token_name)||0)+1));
  const labels=new Map(tokenOptions.map(row=>[String(row.token_id),counts.get(row.token_name)>1?`${row.token_name}（ID ${row.token_id}）`:row.token_name]));
  populateFilter('token','全部令牌',[...labels.keys()].sort((a,b)=>labels.get(a).localeCompare(labels.get(b))),value=>labels.get(value),refreshSelection);
}
function populateGroupFilter(){
  populateFilter('group','全部分组',groupOptions.map(row=>row.group_name),value=>value||'未分组',refreshSelection);
}
// Developer diagnostics remain browser-only and never change Excel exports.
if(developerMode){
  for(const [column,label] of [['cache_hit_rate','缓存命中率'],['amount_per_million','每百万 Token 金额']]){
    const th=document.createElement('th');th.textContent=label;th.dataset.column=column;
    document.querySelector('#usage-table thead tr').append(th);
  }
}
if(failureMode){
  const th=document.createElement('th');th.textContent='失败请求';th.dataset.column='failure_requests';
  document.querySelector('#usage-table thead tr').append(th);
}
function developerCells(tr,row){
  if(!developerMode)return;
  const read=Number(row.cache_read_tokens),denominator=Number(row.input_tokens)+read;
  cell(tr,row.cache_read_tokens!==null&&row.input_tokens!==null&&denominator>0?number(read/denominator*100,2)+'%':'—','','cache_hit_rate');
  const tokens=Number(row.total_tokens);
  cell(tr,tokens>0?tableMoney(Number(row.amount)/tokens*1000000):'—','','amount_per_million');
}
function failureCell(tr,row){
  if(!failureMode)return;
  const lines=Object.entries(row.failure_codes||{}).map(([code,count])=>`${code} ${number(count,0)}次`);
  cell(tr,lines.length?lines.join('\n'):'—','failure-requests','failure_requests');
}
function sumRows(data) {
  const total={amount:0,request_count:0,failure_count:0,failure_codes:{},...Object.fromEntries(tokenFields.map(k=>[k,0]))};
  data.forEach(r=>{
    total.amount+=Number(r.amount);total.request_count+=Number(r.request_count);total.failure_count+=Number(r.failure_count||0);
    tokenFields.forEach(k=>total[k]=total[k]===null||r[k]===null?null:total[k]+Number(r[k]));
    for(const [code,count] of Object.entries(r.failure_codes||{}))total.failure_codes[code]=(total.failure_codes[code]||0)+Number(count);
  });
  return total;
}
function renderSummary(data) {
  const total=sumRows(data);
  $('amount').textContent='¥ '+number(total.amount,2);
  tokenFields.forEach(k=>$(k).textContent=number(total[k],0));
  const seconds=Number(snapshot.totals.duration_seconds);
  $('tpm').textContent=number(total.total_tokens===null?null:total.total_tokens*60/seconds,2);
  $('rpm').textContent=number(total.request_count*60/seconds,4);
  $('counts').textContent=`${new Set(data.map(r=>r.user_id)).size} / ${new Set(data.map(r=>r.model_name)).size}`;
}
function renderDetails() {
  hideMoneyTooltip();
  const data=selectedRows();
  const total=sumRows(data);
  $('rows').replaceChildren();$('totals').replaceChildren();
  for(let i=0;i<data.length;i++) {
    const r=data[i],tr=document.createElement('tr');
    if(i===0 || data[i-1].user_id!==r.user_id || data[i-1].username!==r.username) {
      let end=i+1;while(end<data.length && data[end].user_id===r.user_id && data[end].username===r.username)end++;
      userCell(tr,r).rowSpan=end-i;
    }
    if(detailMode==='token')cell(tr,r.token_name||'未知令牌','token-name');
    cell(tr,number(r.request_count,0),'','request_count');cell(tr,r.model_name,'model','model_name');cell(tr,r.tier_name||'-','','tier_name');
    for(const key of [...tokenFields,'group_ratio','input_price','output_price','cache_price','write_price','amount']){
      const price=['input_price','output_price','cache_price','write_price'].includes(key);
      const td=cell(tr,price?priceDisplay(r,key):key==='amount'?tableMoney(r[key]):number(r[key],tokenFields.includes(key)?0:6),price?'price-breakdown':'',key);
      if(key==='amount')bindMoneyTooltip(td,()=>modelMode==='summary'?totalMoneyFormula(r._sourceRows,r.amount):(r.report_id?lazyRowMoneyFormula(r):rowMoneyFormula(r)));
    }
    developerCells(tr,r);
    failureCell(tr,r);
    $('rows').append(tr);
  }
  if(!data.length){const hidden=modelMode==='summary'?new Set(['model_name','tier_name',...modelSummaryColumns]):new Set(),tr=document.createElement('tr');cell(tr,'该时间范围内暂无消费或失败请求','empty').colSpan=[...visibleColumns()].filter(column=>!hidden.has(column)).length+(detailMode==='token'?1:0)+(developerMode?2:0)+(failureMode?1:0);$('rows').append(tr);}
  const tr=document.createElement('tr');cell(tr,'总计','','username');if(detailMode==='token')cell(tr,'','token-name');cell(tr,number(total.request_count,0),'','request_count');cell(tr,'','','model_name');cell(tr,'','','tier_name');tokenFields.forEach(k=>cell(tr,number(total[k],0),'',k));
  for(const key of ['group_ratio','input_price','output_price','cache_price','write_price'])cell(tr,'','',key);
  bindMoneyTooltip(cell(tr,tableMoney(total.amount),'','amount'),()=>totalMoneyFormula(data,total.amount));
  developerCells(tr,total);
  failureCell(tr,total);
  $('totals').append(tr);
  applyColumnVisibility();
}
function setDetailMode(mode){
  detailMode=mode;
  document.querySelectorAll('[data-detail-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.detailMode===mode)));
}
function setModelMode(mode){
  const toggles=[...document.querySelectorAll('[data-column-toggle]')].filter(input=>modelSummaryColumns.includes(input.dataset.columnToggle));
  if(mode==='summary'&&modelMode!=='summary'){
    modelColumnSelection=new Set(toggles.filter(input=>input.checked).map(input=>input.dataset.columnToggle));
    toggles.forEach(input=>{input.checked=false;input.disabled=true;});
  }else if(mode==='model'&&modelMode==='summary'){
    toggles.forEach(input=>{input.disabled=false;input.checked=modelColumnSelection?.has(input.dataset.columnToggle)??false;});
    modelColumnSelection=null;
  }
  modelMode=mode;
  document.querySelectorAll('[data-model-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.modelMode===mode)));
}
function updateReportStatus(){
  const rows=selectedRows();
  const label=modelMode==='summary'?(detailMode==='token'?'用户令牌汇总':'用户汇总'):(detailMode==='token'?'用户令牌模型汇总':'用户模型汇总');
  $('status').className='';
  $('status').textContent=`${snapshot.start.replace('T',' ')} 至 ${snapshot.end.replace('T',' ')} · ${rows.length} 条${label}`;
  if(snapshot.rows.some(r=>r.metadata_error_count))$('status').textContent+=' · 部分日志元数据损坏：金额保留，无法确认的用量显示为 —';
  if(modelMode==='model'&&snapshot.rows.some(r=>(r.pricing_buckets||r.has_pricing_buckets)&&r.input_price===null))$('status').textContent+=' · 部分请求的历史价格无法还原，显示为 —';
  else if(modelMode==='model'&&snapshot.rows.some(r=>!(r.pricing_buckets||r.has_pricing_buckets)&&r.pricing_mode==='expression'&&!(r.price_tiers?.length||r.price_tier_count)))$('status').textContent+=' · 部分表达式无法拆分价格';
  else if(modelMode==='model'&&snapshot.rows.some(r=>!(r.pricing_buckets||r.has_pricing_buckets)&&r.pricing_mode!=='expression'&&r.pricing_mode!=='unknown'&&['input_price','output_price','cache_price','write_price'].some(k=>r[k]===null)))$('status').textContent+=' · 部分当前价格未配置，显示为 —';
}
async function loadTokenDetails(){
  const ticket=Scope.epoch;
  if(tokenSnapshot||!snapshot)return;
  const expected=`${snapshot.start}\n${snapshot.end}`;
  const params=new URLSearchParams({start:snapshot.start,end:snapshot.end});
  if(failureMode)params.set('dev','2');
  const response=await Scope.request('/statistics/api/usage/by-token?'+params+'&details=lazy');
  if(!response.ok){let msg='分令牌查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  if(snapshot&&expected===`${snapshot.start}\n${snapshot.end}`)tokenSnapshot=result;
}
async function loadTokenOptions(){
  const ticket=Scope.epoch;
  const expected=`${snapshot.start}\n${snapshot.end}`,params=new URLSearchParams({start:snapshot.start,end:snapshot.end});
  if(failureMode)params.set('dev','2');
  const response=await Scope.request('/statistics/api/usage/tokens?'+params);
  if(!response.ok){let msg='令牌列表查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  if(snapshot&&expected===`${snapshot.start}\n${snapshot.end}`)tokenOptions=result.rows;
}
async function loadGroupOptions(){
  const ticket=Scope.epoch;
  const expected=`${snapshot.start}\n${snapshot.end}`,params=new URLSearchParams({start:snapshot.start,end:snapshot.end});
  if(failureMode)params.set('dev','2');
  const response=await Scope.request('/statistics/api/usage/groups?'+params);
  if(!response.ok){let msg='分组列表查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  if(snapshot&&expected===`${snapshot.start}\n${snapshot.end}`)groupOptions=result.rows;
}
async function loadFilteredSelection(){
  const ticket=Scope.epoch;
  const tokenIds=[...selectedFilterValues('token')].sort(),groups=[...selectedFilterValues('group')].sort();
  if(!tokenIds.length&&!groups.length){filteredSelectionSnapshot=null;return;}
  const expected=`${snapshot.start}\n${snapshot.end}\n${detailMode}\n${tokenIds.join(',')}\n${groups.join(',')}`;
  const params=new URLSearchParams({start:snapshot.start,end:snapshot.end});
  if(failureMode)params.set('dev','2');
  tokenIds.forEach(id=>params.append('token_id',id));
  groups.forEach(group=>params.append('group',group));
  if(detailMode==='token')params.set('by_token','1');
  const response=await Scope.request('/statistics/api/usage/by-selection?'+params+'&details=lazy');
  if(!response.ok){let msg='筛选查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  const current=`${snapshot.start}\n${snapshot.end}\n${detailMode}\n${[...selectedFilterValues('token')].sort().join(',')}\n${[...selectedFilterValues('group')].sort().join(',')}`;
  if(expected===current)filteredSelectionSnapshot=result;
}
async function refreshSelection(){
  const ticket=Scope.epoch;
  filteredSelectionSnapshot=null;
  if(!snapshot)return;
  $('status').className='';$('status').textContent='正在应用筛选…';
  try{await loadFilteredSelection();Scope.guard(ticket);renderDetails();updateReportStatus();}
  catch(error){if(ticket!==Scope.epoch)return;$('status').className='error';$('status').textContent=error.message;}
}
document.querySelectorAll('[data-detail-mode]').forEach(button=>button.addEventListener('click',async()=>{
  const ticket=Scope.epoch;
  const mode=button.dataset.detailMode;
  if(mode===detailMode)return;
  setDetailMode(mode);
  filteredSelectionSnapshot=null;
  document.querySelectorAll('[data-detail-mode]').forEach(item=>item.disabled=true);
  $('status').className='';$('status').textContent='正在切换明细…';
  try{
    if(selectedFilterValues('token').size||selectedFilterValues('group').size)await loadFilteredSelection();
    else if(mode==='token')await loadTokenDetails();
    Scope.guard(ticket);renderDetails();updateReportStatus();
  }
  catch(error){if(ticket!==Scope.epoch)return;setDetailMode('summary');renderDetails();$('status').className='error';$('status').textContent=error.message;}
  finally{if(ticket===Scope.epoch)document.querySelectorAll('[data-detail-mode]').forEach(item=>item.disabled=false);}
}));
document.querySelectorAll('[data-model-mode]').forEach(button=>button.addEventListener('click',()=>{
  const mode=button.dataset.modelMode;
  if(mode===modelMode)return;
  setModelMode(mode);
  if(snapshot){renderDetails();updateReportStatus();}
  else applyColumnVisibility();
}));
function render() {
  const data=snapshot.rows;
  renderSummary(data);
  renderDetails();
  renderRanking(data);
  updateReportStatus();
}
function renderRanking(data) {
  const models=new Map();data.forEach(r=>models.set(r.model_name,(models.get(r.model_name)||0)+Number(r.amount)));
  const tokens=ranking==='user_tokens';
  const entries=ranking==='model_amount'?[...models].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])):
    rankingsForSelection(data,ranking).map(r=>[r.username,r[tokens?'total_tokens':'amount']===null?null:Number(r[tokens?'total_tokens':'amount'])]);
  const max=Math.max(...entries.map(e=>e[1]),1);
  $('chart').replaceChildren();
  if(!entries.length){$('chart').textContent='该时间范围内暂无消费记录';return;}
  let rank=0;
  for(const [model,amount] of entries){
    const row=document.createElement('div');row.className='bar-row';
    const label=document.createElement('span');label.className='bar-label';label.textContent=`${++rank}. ${model}`;label.title=model;
    const track=document.createElement('div');track.className='bar-track';const bar=document.createElement('div');bar.className='bar';bar.style.width=`${(amount||0)/max*100}%`;track.append(bar);
    const val=document.createElement('span');val.className='bar-value';val.textContent=tokens?number(amount,0)+' Token':'¥ '+number(amount,2);row.append(label,track,val);$('chart').append(row);
  }
}
function rankingsForSelection(data,type){
  const field=type==='user_tokens'?'total_tokens':'amount',users=new Map();
  data.forEach(row=>users.set(row.username,users.get(row.username)===null||row[field]===null?null:(users.get(row.username)||0)+Number(row[field])));
  return [...users].map(([username,value])=>({username,[field]:value})).sort((a,b)=>(a[field]===null)-(b[field]===null)||(b[field]||0)-(a[field]||0)||a.username.localeCompare(b.username));
}
const tabs=[...document.querySelectorAll('[data-ranking]')];
tabs.forEach((tab,i)=>{
  tab.addEventListener('click',()=>{
    ranking=tab.dataset.ranking;
    tabs.forEach(t=>{t.setAttribute('aria-selected',String(t===tab));t.tabIndex=t===tab?0:-1;});
    $('chart').setAttribute('aria-labelledby',tab.id);
    if(snapshot)render();
  });
  tab.addEventListener('keydown',event=>{
    let target;
    if(event.key==='ArrowRight')target=tabs[(i+1)%tabs.length];
    if(event.key==='ArrowLeft')target=tabs[(i+tabs.length-1)%tabs.length];
    if(event.key==='Home')target=tabs[0];
    if(event.key==='End')target=tabs[tabs.length-1];
    if(target){event.preventDefault();target.focus();target.click();}
  });
});
async function query(event){
  event?.preventDefault();if(!Scope.current||$('submit').disabled)return;
  const ticket=Scope.epoch;
  $('submit').disabled=true;document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=true);
  $('export').disabled=true;$('status').className='';$('status').textContent='正在查询…';
  const params=new URLSearchParams({start:$('start').value,end:$('end').value});
  if(failureMode)params.set('dev','2');
  try{
    const response=await Scope.request('/statistics/api/usage?'+params+'&details=lazy');
    if(!response.ok){let msg='查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
    const result=await response.json();Scope.guard(ticket);snapshot=result;tokenSnapshot=null;filteredSelectionSnapshot=null;tokenOptions=[];groupOptions=[];
    const userNames=[...new Set(snapshot.rows.map(r=>r.username))].sort();
    const displayNames=new Map(snapshot.rows.map(r=>[r.username,r.display_name]));
    populateFilter('user','全部用户',userNames,name=>displayNames.get(name)?`${name}（${displayNames.get(name)}）`:name);
    populateFilter('model','全部模型',[...new Set(snapshot.rows.map(r=>r.model_name))].sort(),name=>name);
    await Promise.all([loadTokenOptions(),loadGroupOptions()]);Scope.guard(ticket);populateTokenFilter();populateGroupFilter();
    if(selectedFilterValues('token').size||selectedFilterValues('group').size)await loadFilteredSelection();
    else if(detailMode==='token')await loadTokenDetails();
    Scope.guard(ticket);$('updated').textContent='更新于 '+snapshot.updated_at.replace('T',' ').slice(0,19)+' 北京时间';render();$('export').disabled=false;
  }catch(error){if(ticket!==Scope.epoch)return;$('status').className='error';$('status').textContent=error.message;}
  finally{if(ticket===Scope.epoch){$('submit').disabled=false;document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=false);}}
}
document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{
  const range=presetRange(button.dataset.preset);$('start').value=range.start;$('end').value=range.end;
  document.querySelectorAll('[data-preset]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  query();
}));
for(const id of ['start','end'])$(id).addEventListener('input',()=>document.querySelectorAll('[data-preset]').forEach(b=>b.setAttribute('aria-pressed','false')));
$('query').addEventListener('submit',query);
document.querySelectorAll('[data-column-toggle]').forEach(input=>input.addEventListener('change',()=>{
  if(!document.querySelector('[data-column-toggle]:checked'))input.checked=true;
  saveVisibleColumns();applyColumnVisibility();
}));
$('show-all-columns').addEventListener('click',()=>{document.querySelectorAll('[data-column-toggle]:not(:disabled)').forEach(input=>input.checked=true);saveVisibleColumns();applyColumnVisibility();});
$('export').addEventListener('click',()=>{if(snapshot)window.location.assign(Scope.url('/statistics/api/export?'+new URLSearchParams({start:snapshot.start,end:snapshot.end})));});
loadVisibleColumns();applyColumnVisibility();
// Reset every ledger-dependent view before starting requests for the next ledger.
window.addEventListener('scopechange',()=>{
  hideMoneyTooltip();
  snapshot=null;tokenSnapshot=null;filteredSelectionSnapshot=null;tokenOptions=[];groupOptions=[];
  for(const key of ['user','model','token','group'])populateFilter(key,{user:'全部用户',model:'全部模型',token:'全部令牌',group:'全部分组'}[key],[],x=>x);
  for(const id of ['rows','totals','chart'])$(id).replaceChildren();
  document.querySelectorAll('.metrics strong').forEach(el=>el.textContent='—');
  $('updated').textContent='北京时间';$('export').disabled=true;$('submit').disabled=false;
  document.querySelectorAll('[data-detail-mode]').forEach(el=>el.disabled=false);
  query();
});
$('export').disabled=true;
window.addEventListener('DOMContentLoaded',()=>Scope.init(),{once:true});
