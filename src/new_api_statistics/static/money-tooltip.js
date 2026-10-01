/* One viewport-positioned tooltip supports hover, keyboard focus and touch. */
const moneyTip=document.createElement('div');
moneyTip.id='money-tooltip';moneyTip.className='money-tooltip';moneyTip.role='tooltip';moneyTip.hidden=true;
document.body.append(moneyTip);
let moneyAnchor=null,moneyHideTimer;
const moneyNumber=n=>n===null||n===undefined?'未配置':Number(n).toLocaleString('zh-CN',{maximumFractionDigits:9});
function rowMoneyFormula(row){
  const f=row.cost_formula;
  if(!f)return [row.model_name,'暂无计算信息'];
  if(f.mode==='historical'){
    const lines=[`${row.username} · ${row.model_name}`,
      `${f.matched_tier?'历史价格匹配当前档位':'请求发生时的价格'}；单位：元 / 百万 Token`,
      `档位：${row.tier_name||'-'} · ${moneyNumber(row.request_count)} 次请求`,
      `总 Token ${moneyNumber(row.total_tokens)}；输入 ${moneyNumber(row.input_tokens)}；输出 ${moneyNumber(row.output_tokens)}；缓存读 ${moneyNumber(row.cache_read_tokens)}；缓存写 ${moneyNumber(row.cache_write_tokens)}`];
    for(const [label,key] of [['输入','input_price'],['输出','output_price'],['缓存读','cache_price'],['缓存写（5 分钟）','write_price']])
      lines.push(`${label}价格：${moneyNumber(f.prices[key])}`);
    for(const b of f.buckets){
      lines.push(`${b.current_ratio?'当前':'历史'}倍率 ${moneyNumber(b.ratio)} · ${moneyNumber(b.request_count)} 次请求`);
      if(b.terms.length){
        const terms=b.terms.map(t=>t.tokens===0&&t.price===null?'0':`${moneyNumber(t.tokens)} × ${moneyNumber(t.price)}`);
        lines.push(`(${terms.join(' + ')}) × ${moneyNumber(b.ratio)} ÷ 1,000,000`);
      }
      lines.push(`试算：${b.calculated===null?'无法计算':'¥ '+moneyNumber(b.calculated)}；日志实际：¥ ${moneyNumber(b.actual)}`);
    }
    lines.push(`试算合计：${f.calculated===null?'无法完整计算':'¥ '+moneyNumber(f.calculated)}`);
    lines.push(`实际消费金额：¥ ${moneyNumber(row.amount)}`);
    if(f.difference!==null)lines.push(`差额（实际 − 试算）：¥ ${moneyNumber(f.difference)}`);
    if(f.converted)lines.push('历史倍率与当前分组倍率不一致时，缓存读取 Token 按当前价格和倍率数学配平。');
    lines.push('实际金额来自消费日志；逐请求配额取整可能产生微小尾差。');
    return lines;
  }
  if(f.mode==='expression'){
    const lines=[`${row.username} · ${row.model_name}`,'当前表达式价格；单位：元 / 百万 Token'];
    if(!f.tiers.length)lines.push('表达式含复杂规则，无法安全拆分为固定 Token 单价。');
    for(const t of f.tiers){
      lines.push(`${t.name}${t.condition?`（${t.condition}）`:''}`);
      for(const [label,key] of [['输入','input_price'],['输出','output_price'],['缓存读','cache_price'],['缓存写（按 5 分钟档）','write_price']]){
        if(t[key]!==null)lines.push(`${label}：${moneyNumber(t[key])}`);
      }
    }
    lines.push(`档位：${row.tier_name||'-'} · ${moneyNumber(row.request_count)} 次请求`);
    lines.push(`总 Token ${moneyNumber(row.total_tokens)}；输入 ${moneyNumber(row.input_tokens)}；输出 ${moneyNumber(row.output_tokens)}；缓存读 ${moneyNumber(row.cache_read_tokens)}；缓存写 ${moneyNumber(row.cache_write_tokens)}`);
    for(const b of f.buckets||[]){
      if(f.buckets.length>1)lines.push(`倍率 ${moneyNumber(b.ratio)}：${moneyNumber(b.request_count)} 次请求`);
      if(b.terms.length){
        const terms=b.terms.map(t=>t.tokens===0&&t.price===null?'0':`${moneyNumber(t.tokens)} × ${moneyNumber(t.price)}`);
        lines.push(`(${terms.join(' + ')}) × ${moneyNumber(b.ratio)} ÷ 1,000,000`);
      }
      lines.push(`该倍率试算：${b.calculated===null?'无法计算（档位或单价不匹配）':'¥ '+moneyNumber(b.calculated)}；日志实际：¥ ${moneyNumber(b.actual)}`);
    }
    lines.push(`当前价格试算合计：${f.calculated===null?'无法完整计算':'¥ '+moneyNumber(f.calculated)}`);
    lines.push(`实际消费金额：¥ ${moneyNumber(row.amount)}`);
    if(f.difference!==null)lines.push(`差额（实际 − 计算）：¥ ${moneyNumber(f.difference)}`);
    lines.push('缓存写入统一按 5 分钟档试算；当前价格试算不等于历史计费重放。');
    return lines;
  }
  const lines=[`${row.username} · ${row.model_name}`,'单价单位：元 / 百万 Token'];
  for(const t of f.terms)lines.push(`${t.label}：${moneyNumber(t.tokens)} × ${moneyNumber(t.price)}${t.tokens===0&&t.price===null?'（用量为 0，不计费）':''}`);
  const terms=f.terms.map(t=>t.tokens===0&&t.price===null?'0':`${moneyNumber(t.tokens)} × ${moneyNumber(t.price)}`);
  lines.push(`(${terms.join(' + ')}) × ${moneyNumber(f.ratio)} ÷ 1,000,000`);
  lines.push(`计算金额：${f.calculated===null?'无法完整计算（缺少单价或倍率）':'¥ '+moneyNumber(f.calculated)}`);
  lines.push(`实际消费金额：¥ ${moneyNumber(row.amount)}`);
  if(f.difference!==null)lines.push(`差额（实际 − 计算）：¥ ${moneyNumber(f.difference)}`);
  lines.push('计算使用当前单价和倍率；实际金额来自消费日志汇总。');
  if(f.converted)lines.push('缓存读使用数学折算后的整数，四舍五入可能产生尾差。');
  else if(f.ratio_count>1)lines.push('区间存在多种历史倍率，按当前显示倍率重算可能与实际金额不同。');
  return lines;
}
function totalMoneyFormula(rows,total){
  return ['当前明细消费金额合计',...rows.map((r,i)=>`${i+1}. ${r.username} · ${r.model_name}：¥ ${moneyNumber(r.amount)}`),
    `${rows.length?rows.map(r=>moneyNumber(r.amount)).join(' + '):'0'} = ${moneyNumber(total)}`];
}
function hideMoneyTooltip(){
  clearTimeout(moneyHideTimer);
  moneyAnchor?.removeAttribute('aria-describedby');moneyAnchor=null;moneyTip.hidden=true;
}
function placeMoneyTooltip(){
  if(!moneyAnchor)return;
  const rect=moneyAnchor.getBoundingClientRect(),tip=moneyTip.getBoundingClientRect(),gap=8;
  const left=Math.max(gap,Math.min(rect.right-tip.width,innerWidth-tip.width-gap));
  const top=rect.top-tip.height-gap>=gap?rect.top-tip.height-gap:Math.max(gap,Math.min(rect.bottom+gap,innerHeight-tip.height-gap));
  moneyTip.style.left=`${left}px`;moneyTip.style.top=`${top}px`;
}
function showMoneyTooltip(anchor,content){
  hideMoneyTooltip();moneyAnchor=anchor;
  moneyTip.replaceChildren(...content().map((line,i)=>{const p=document.createElement('p');p.textContent=line;if(i===0)p.className='money-title';return p;}));
  moneyTip.hidden=false;moneyTip.scrollTop=0;anchor.setAttribute('aria-describedby',moneyTip.id);placeMoneyTooltip();
}
function bindMoneyTooltip(anchor,content){
  anchor.classList.add('money-cell');anchor.tabIndex=0;
  anchor.addEventListener('pointerenter',()=>showMoneyTooltip(anchor,content));
  anchor.addEventListener('focus',()=>showMoneyTooltip(anchor,content));
  anchor.addEventListener('click',()=>showMoneyTooltip(anchor,content));
  anchor.addEventListener('pointerleave',()=>{moneyHideTimer=setTimeout(hideMoneyTooltip,200);});
  anchor.addEventListener('blur',hideMoneyTooltip);
}
moneyTip.addEventListener('pointerenter',()=>clearTimeout(moneyHideTimer));
moneyTip.addEventListener('pointerleave',()=>{moneyHideTimer=setTimeout(hideMoneyTooltip,200);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hideMoneyTooltip();});
document.addEventListener('pointerdown',e=>{if(moneyAnchor&&!moneyTip.contains(e.target)&&!moneyAnchor.contains(e.target))hideMoneyTooltip();});
document.addEventListener('scroll',e=>{
  if(e.target===moneyTip||!moneyAnchor)return;
  const rect=moneyAnchor.getBoundingClientRect();
  if(rect.bottom<0||rect.top>innerHeight||rect.right<0||rect.left>innerWidth)hideMoneyTooltip();
  else placeMoneyTooltip();
},true);
window.addEventListener('resize',hideMoneyTooltip);
