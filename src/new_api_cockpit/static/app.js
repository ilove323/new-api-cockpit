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
const priceFields = ['input_price','output_price','cache_price','write_price'];
const modelSummaryColumns = ['group_ratio',...priceFields];
const numericDetailColumns = [...tokenFields,...modelSummaryColumns,'amount'];
const detailColumns = new Set(['username','request_count','model_name','tier_name',...numericDetailColumns,'cache_hit_rate','amount_per_million','failure_requests']);
const columnStorageKey = 'new-api-cockpit.visible-columns.v2';
let modelColumnSelection = null;
let snapshotIncludesFailures = false;
const failureMode = () => $('column-failure-requests').checked;
const failureOnly = row => Number(row.request_count)===0&&Number(row.failure_count)>0;
const numberFormats = new Map();
function number(n,digits=6){
  if(n===null||n===undefined)return '—';
  if(!numberFormats.has(digits))numberFormats.set(digits,new Intl.NumberFormat('zh-CN',{maximumFractionDigits:digits}));
  return numberFormats.get(digits).format(Number(n));
}
// Presentation only: never round the source values used by calculations or tooltips.
const moneyFormat = new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const tableMoney = n => n === null || n === undefined ? '—' : moneyFormat.format(Number(n));
function priceDisplay(row,key){
  return !(row.pricing_buckets||row.has_pricing_buckets)&&row.pricing_mode==='expression'&&!(row.price_tiers?.length||row.price_tier_count)?'无法拆分':tableMoney(row[key]);
}
const cell = (tr, value, cls='', column='') => {const td=document.createElement('td');td.textContent=value;td.className=cls;if(column)td.dataset.column=column;tr.append(td);return td;};
const userCell = (tr,row) => {
  const td=cell(tr,row.username,'username','username');
  if(row.display_name){const name=document.createElement('span');name.className='display-name';name.textContent=row.display_name;td.append(name);}
  return td;
};
function reportBoundaryMilliseconds(value,isEnd=false){
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$/.test(value))return NaN;
  let time=value.replace(' ','T');
  if(time.length===10)time+=isEnd?'T23:59:59':'T00:00:00';
  return Date.parse(time+'+08:00');
}
function reportLogBounds(){
  // Use the displayed report snapshot, not unsubmitted inputs or browser timezone.
  // New API uses milliseconds in its URL and includes the selected end second.
  const start=reportBoundaryMilliseconds(snapshot?.start),end=reportBoundaryMilliseconds(snapshot?.end,true);
  return Number.isFinite(start)&&Number.isFinite(end)&&end>=start?{start,end}:null;
}
function newApiLogsURL(row,bounds=reportLogBounds()){
  if(typeof row.username!=='string'||!row.username.trim()||!bounds)return null;
  const {start,end}=bounds;
  const params=new URLSearchParams({username:row.username,type:'0',page:'1',startTime:String(start),endTime:String(end)});
  if(row.model_name)params.set('model',row.model_name);
  return '/usage-logs/common?'+params;
}
function logsCell(tr,row,bounds){
  const td=cell(tr,'','','logs'),url=newApiLogsURL(row,bounds);
  if(!url){td.textContent='—';return;}
  const link=document.createElement('a');
  link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.className='usage-log-link';link.textContent='查看日志';
  link.title=`查看 ${row.username}${row.model_name?' · '+row.model_name:''} 在当前统计周期内的全部日志，不限档位或令牌`;
  link.setAttribute('aria-label',`${link.title}（新标签页）`);
  td.append(link);
}
function aggregateModels(data){
  const grouped=new Map();
  for(const row of data){
    const key=[row.user_id,row.username,detailMode==='token'?row.token_id:''].join('\u0000');
    if(!grouped.has(key)){
      grouped.set(key,{...row,model_name:'',amount:0,request_count:0,failure_count:0,failure_codes:{},...Object.fromEntries(tokenFields.map(field=>[field,0])),...Object.fromEntries(modelSummaryColumns.map(field=>[field,null])),_sourceRows:[]});
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
  const filtered=rows.filter(r=>(failureMode()||!failureOnly(r))&&(!users.size||users.has(r.username))&&(!models.size||models.has(r.model_name)));
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
function hiddenModelColumns(){
  return new Set(modelMode==='summary'?['model_name','tier_name',...modelSummaryColumns]:[]);
}
function visibleColumnCount(visible=visibleColumns(),hidden=hiddenModelColumns()){
  return [...visible].filter(column=>!hidden.has(column)).length+(detailMode==='token'?1:0)+1;
}
function applyColumnVisibility(){
  const visible=visibleColumns();
  const hiddenByMode=hiddenModelColumns();
  document.querySelectorAll('#usage-table [data-column]').forEach(element=>{
    if(detailColumns.has(element.dataset.column))element.hidden=!visible.has(element.dataset.column)||hiddenByMode.has(element.dataset.column);
  });
  document.querySelectorAll('#usage-table [data-token-column]').forEach(element=>element.hidden=detailMode!=='token');
  const columns=visibleColumnCount(visible,hiddenByMode);
  document.querySelectorAll('#usage-table .empty').forEach(element=>element.colSpan=columns);
  $('usage-table').style.setProperty('--usage-table-min-width',Math.max(480,columns*115)+'px');
}
function loadVisibleColumns(){
  let saved;
  try{
    saved=JSON.parse(localStorage.getItem(columnStorageKey));
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
function populateTokenFilter(){
  const counts=new Map();
  tokenOptions.forEach(row=>counts.set(row.token_name,(counts.get(row.token_name)||0)+1));
  const labels=new Map(tokenOptions.map(row=>[String(row.token_id),counts.get(row.token_name)>1?`${row.token_name}（ID ${row.token_id}）`:row.token_name]));
  populateFilter('token','全部令牌',[...labels.keys()].sort((a,b)=>labels.get(a).localeCompare(labels.get(b))),value=>labels.get(value),refreshSelection);
}
function populateGroupFilter(){
  populateFilter('group','全部分组',groupOptions.map(row=>row.group_name),value=>value||'未分组',refreshSelection);
}
// Optional table columns never change Excel exports.
function optionalCells(tr,row){
  const read=Number(row.cache_read_tokens),denominator=Number(row.input_tokens)+read;
  cell(tr,row.cache_read_tokens!==null&&row.input_tokens!==null&&denominator>0?number(read/denominator*100,2)+'%':'—','','cache_hit_rate');
  const tokens=Number(row.total_tokens);
  cell(tr,tokens>0?tableMoney(Number(row.amount)/tokens*1000000):'—','','amount_per_million');
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
function renderDetails(data=selectedRows()) {
  hideMoneyTooltip();
  const total=sumRows(data);
  const rows=$('rows'),totals=$('totals'),bounds=reportLogBounds();
  rows.replaceChildren();totals.replaceChildren();
  for(let i=0;i<data.length;i++) {
    const r=data[i],tr=document.createElement('tr');
    if(i===0 || data[i-1].user_id!==r.user_id || data[i-1].username!==r.username) {
      let end=i+1;while(end<data.length && data[end].user_id===r.user_id && data[end].username===r.username)end++;
      userCell(tr,r).rowSpan=end-i;
    }
    if(detailMode==='token')cell(tr,r.token_name||'未知令牌','token-name');
    cell(tr,number(r.request_count,0),'','request_count');cell(tr,r.model_name,'model','model_name');cell(tr,r.tier_name||'-','','tier_name');
    for(const key of numericDetailColumns){
      const price=priceFields.includes(key);
      const td=cell(tr,price?priceDisplay(r,key):key==='amount'?tableMoney(r[key]):number(r[key],tokenFields.includes(key)?0:6),price?'price-breakdown':'',key);
      if(key==='amount')bindMoneyTooltip(td,()=>modelMode==='summary'?totalMoneyFormula(r._sourceRows,r.amount):(r.report_id?lazyRowMoneyFormula(r):rowMoneyFormula(r)));
    }
    optionalCells(tr,r);
    logsCell(tr,r,bounds);
    rows.append(tr);
  }
  if(!data.length){const tr=document.createElement('tr');cell(tr,failureMode()?'该时间范围内暂无消费或失败请求':'该时间范围内暂无消费记录','empty').colSpan=visibleColumnCount();rows.append(tr);}
  const tr=document.createElement('tr');cell(tr,'总计','','username');if(detailMode==='token')cell(tr,'','token-name');cell(tr,number(total.request_count,0),'','request_count');cell(tr,'','','model_name');cell(tr,'','','tier_name');tokenFields.forEach(k=>cell(tr,number(total[k],0),'',k));
  for(const key of modelSummaryColumns)cell(tr,'','',key);
  bindMoneyTooltip(cell(tr,tableMoney(total.amount),'','amount'),()=>totalMoneyFormula(data,total.amount));
  optionalCells(tr,total);
  cell(tr,'','','logs');
  totals.append(tr);
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
function updateReportStatus(rows=selectedRows()){
  const label=modelMode==='summary'?(detailMode==='token'?'用户令牌汇总':'用户汇总'):(detailMode==='token'?'用户令牌模型汇总':'用户模型汇总');
  $('status').className='';
  $('status').textContent=`${snapshot.start.replace('T',' ')} 至 ${snapshot.end.replace('T',' ')} · ${rows.length} 条${label}`;
  if(snapshot.rows.some(r=>r.metadata_error_count))$('status').textContent+=' · 部分日志元数据损坏：金额保留，无法确认的用量显示为 —';
  if(modelMode==='model'&&snapshot.rows.some(r=>(r.pricing_buckets||r.has_pricing_buckets)&&r.input_price===null))$('status').textContent+=' · 部分请求的历史价格无法还原，显示为 —';
  else if(modelMode==='model'&&snapshot.rows.some(r=>!(r.pricing_buckets||r.has_pricing_buckets)&&r.pricing_mode==='expression'&&!(r.price_tiers?.length||r.price_tier_count)))$('status').textContent+=' · 部分表达式无法拆分价格';
  else if(modelMode==='model'&&snapshot.rows.some(r=>!(r.pricing_buckets||r.has_pricing_buckets)&&r.pricing_mode!=='expression'&&r.pricing_mode!=='unknown'&&priceFields.some(k=>r[k]===null)))$('status').textContent+=' · 部分当前价格未配置，显示为 —';
}
function renderDetailView(){
  const rows=selectedRows();
  renderDetails(rows);updateReportStatus(rows);
}
function reportParams(){
  const params=new URLSearchParams({start:snapshot.start,end:snapshot.end});
  if(snapshotIncludesFailures)params.set('include_failures','1');
  return params;
}
async function loadTokenDetails(){
  const ticket=Scope.epoch;
  if(tokenSnapshot||!snapshot)return;
  const expected=snapshot,params=reportParams();
  const response=await Scope.request('/cockpit/statistics/api/usage/by-token?'+params+'&details=lazy');
  if(!response.ok){let msg='分令牌查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  if(expected===snapshot)tokenSnapshot=result;
}
async function loadTokenOptions(){
  const ticket=Scope.epoch;
  const expected=snapshot,params=reportParams();
  const response=await Scope.request('/cockpit/statistics/api/usage/tokens?'+params);
  if(!response.ok){let msg='令牌列表查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  if(expected===snapshot)tokenOptions=result.rows;
}
async function loadGroupOptions(){
  const ticket=Scope.epoch;
  const expected=snapshot,params=reportParams();
  const response=await Scope.request('/cockpit/statistics/api/usage/groups?'+params);
  if(!response.ok){let msg='分组列表查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  if(expected===snapshot)groupOptions=result.rows;
}
async function loadFilteredSelection(){
  const ticket=Scope.epoch;
  const tokenIds=[...selectedFilterValues('token')].sort(),groups=[...selectedFilterValues('group')].sort();
  if(!tokenIds.length&&!groups.length){filteredSelectionSnapshot=null;return;}
  const report=snapshot,expected=`${detailMode}\n${tokenIds.join(',')}\n${groups.join(',')}`;
  const params=reportParams();
  tokenIds.forEach(id=>params.append('token_id',id));
  groups.forEach(group=>params.append('group',group));
  if(detailMode==='token')params.set('by_token','1');
  const response=await Scope.request('/cockpit/statistics/api/usage/by-selection?'+params+'&details=lazy');
  if(!response.ok){let msg='筛选查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
  const result=await response.json();Scope.guard(ticket);
  const current=`${detailMode}\n${[...selectedFilterValues('token')].sort().join(',')}\n${[...selectedFilterValues('group')].sort().join(',')}`;
  if(report===snapshot&&expected===current)filteredSelectionSnapshot=result;
}
async function refreshSelection(){
  const ticket=Scope.epoch;
  filteredSelectionSnapshot=null;
  if(!snapshot)return;
  $('status').className='';$('status').textContent='正在应用筛选…';
  try{await loadFilteredSelection();Scope.guard(ticket);renderDetailView();}
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
    Scope.guard(ticket);renderDetailView();
  }
  catch(error){if(ticket!==Scope.epoch)return;setDetailMode('summary');renderDetails();$('status').className='error';$('status').textContent=error.message;}
  finally{if(ticket===Scope.epoch)document.querySelectorAll('[data-detail-mode]').forEach(item=>item.disabled=false);}
}));
document.querySelectorAll('[data-model-mode]').forEach(button=>button.addEventListener('click',()=>{
  const mode=button.dataset.modelMode;
  if(mode===modelMode)return;
  setModelMode(mode);
  if(snapshot)renderDetailView();
  else applyColumnVisibility();
}));
function render() {
  // Failure-only dimensions belong to the optional detail column, not usage metrics/rankings.
  const data=snapshot.rows.filter(row=>!failureOnly(row));
  renderSummary(data);
  renderDetailView();
  renderRanking(data);
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
async function query(event,range){
  event?.preventDefault();if(!Scope.current||$('submit').disabled)return;
  const ticket=Scope.epoch,includeFailures=failureMode();
  $('submit').disabled=true;document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=true);
  $('column-failure-requests').disabled=true;
  $('export').disabled=true;$('status').className='';$('status').textContent='正在查询…';
  const params=new URLSearchParams(range||{start:$('start').value,end:$('end').value});
  if(includeFailures)params.set('include_failures','1');
  try{
    const response=await Scope.request('/cockpit/statistics/api/usage?'+params+'&details=lazy');
    if(!response.ok){let msg='查询失败，请重试';try{msg=(await response.json()).error||msg;}catch{}throw new Error(msg);}
    const result=await response.json();Scope.guard(ticket);snapshot=result;snapshotIncludesFailures=includeFailures;tokenSnapshot=null;filteredSelectionSnapshot=null;tokenOptions=[];groupOptions=[];
    const userNames=[...new Set(snapshot.rows.map(r=>r.username))].sort();
    const displayNames=new Map(snapshot.rows.map(r=>[r.username,r.display_name]));
    populateFilter('user','全部用户',userNames,name=>displayNames.get(name)?`${name}（${displayNames.get(name)}）`:name);
    populateFilter('model','全部模型',[...new Set(snapshot.rows.map(r=>r.model_name))].sort(),name=>name);
    await Promise.all([loadTokenOptions(),loadGroupOptions()]);Scope.guard(ticket);populateTokenFilter();populateGroupFilter();
    if(selectedFilterValues('token').size||selectedFilterValues('group').size)await loadFilteredSelection();
    else if(detailMode==='token')await loadTokenDetails();
    Scope.guard(ticket);$('updated').textContent='更新于 '+snapshot.updated_at.replace('T',' ').slice(0,19)+' 北京时间';render();$('export').disabled=false;
  }catch(error){
    if(ticket!==Scope.epoch)return;
    if(snapshot){$('column-failure-requests').checked=snapshotIncludesFailures;saveVisibleColumns();renderDetails();$('export').disabled=false;}
    $('status').className='error';$('status').textContent=error.message;
  }
  finally{if(ticket===Scope.epoch){$('submit').disabled=false;$('column-failure-requests').disabled=false;document.querySelectorAll('[data-preset]').forEach(b=>b.disabled=false);}}
}
document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{
  const range=presetRange(button.dataset.preset);$('start').value=range.start;$('end').value=range.end;
  document.querySelectorAll('[data-preset]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  query();
}));
for(const id of ['start','end'])$(id).addEventListener('input',()=>document.querySelectorAll('[data-preset]').forEach(b=>b.setAttribute('aria-pressed','false')));
$('query').addEventListener('submit',query);
function columnSelectionChanged(){
  saveVisibleColumns();applyColumnVisibility();
  // Use the displayed report's range, not unsubmitted edits in the time inputs.
  if(snapshot&&failureMode()!==snapshotIncludesFailures)return query(undefined,{start:snapshot.start,end:snapshot.end});
}
document.querySelectorAll('[data-column-toggle]').forEach(input=>input.addEventListener('change',()=>{
  if(!document.querySelector('[data-column-toggle]:checked'))input.checked=true;
  return columnSelectionChanged();
}));
$('show-all-columns').addEventListener('click',()=>{document.querySelectorAll('[data-column-toggle]:not(:disabled)').forEach(input=>input.checked=true);return columnSelectionChanged();});
$('export').addEventListener('click',async()=>{if(!snapshot)return;try{await window.CockpitAuth?.ensure();if(snapshot)window.location.assign(Scope.url('/cockpit/statistics/api/export?'+new URLSearchParams({start:snapshot.start,end:snapshot.end})));}catch(error){$('status').className='error';$('status').textContent=error.message;}});
loadVisibleColumns();applyColumnVisibility();
// Reset every ledger-dependent view before starting requests for the next ledger.
window.addEventListener('scopechange',()=>{
  hideMoneyTooltip();
  snapshot=null;snapshotIncludesFailures=false;tokenSnapshot=null;filteredSelectionSnapshot=null;tokenOptions=[];groupOptions=[];
  for(const key of ['user','model','token','group'])populateFilter(key,{user:'全部用户',model:'全部模型',token:'全部令牌',group:'全部分组'}[key],[],x=>x);
  for(const id of ['rows','totals','chart'])$(id).replaceChildren();
  document.querySelectorAll('.metrics strong').forEach(el=>el.textContent='—');
  $('updated').textContent='北京时间';$('export').disabled=true;$('submit').disabled=false;
  document.querySelectorAll('[data-detail-mode]').forEach(el=>el.disabled=false);
  query();
});
$('export').disabled=true;
window.addEventListener('DOMContentLoaded',()=>Scope.init(),{once:true});
