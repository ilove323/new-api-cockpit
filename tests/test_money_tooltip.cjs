const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function formulaLines(row,{rate='1',currency='CNY'}={}) {
  const context = {
    document: {
      createElement: () => ({addEventListener() {}}),
      body: {append() {}},
      addEventListener() {},
    },
    window: {addEventListener() {}},
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/new_api_cockpit/static/report-currency.js'),'utf8'),context);
  context.window.CockpitReportCurrency.configure({usd_cny_rate:rate});
  context.window.CockpitReportCurrency.select(currency);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/new_api_cockpit/static/money-tooltip.js'), 'utf8'),
    context,
  );
  return context.rowMoneyFormula(row);
}

test('historical-price tooltip displays one tier and separate ratio calculations', () => {
  const rows = formulaLines({
    username: 'tester', model_name: 'gpt', tier_name: 'low',
    request_count: 3, total_tokens: 300, input_tokens: 30,
    output_tokens: 3, cache_read_tokens: 267, cache_write_tokens: 0,
    amount: '0.00048',
    cost_formula: {
      mode: 'historical', matched_tier: true,
      prices: {input_price: '2', output_price: '10', cache_price: '0.2', write_price: null},
      buckets: [
        {ratio: '3.4', request_count: 2, actual: '0.00032',
         calculated: '0.00032028', terms: [{tokens: 20, price: '2'}]},
        {ratio: '3', request_count: 1, actual: '0.00016',
         calculated: '0.00016', terms: [{tokens: 10, price: '2'}]},
      ],
      calculated: '0.00048028', difference: '-0.00000028',
    },
  });
  assert.equal(rows.filter(line => line.startsWith('档位：')).length, 1);
  assert.equal(rows.filter(line => line.startsWith('请求倍率 ')).length, 2);
  assert(rows.some(line => line.includes('实际消费金额')));
  assert(rows.some(line => line.includes('实际消费金额：¥ 0.00048')));
  assert(rows.some(line => line.includes('试算合计：¥ 0.00048028')));
});
test('bad log metadata explains retained charges without a guessed calculation',()=>{
  const lines=formulaLines({username:'fixture',model_name:'gpt',amount:'1.234567',cost_formula:{mode:'unknown',calculated:null}});
  assert(lines.some(line=>line.includes('¥ 1.234567')));
  assert(lines.some(line=>line.includes('无法确认')));
  assert(!lines.some(line=>line.includes('×')));
});
function tooltipHarness(){
  const events={};
  function element(){return {children:[],style:{},classList:{add(){}},attrs:{},addEventListener(){},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},replaceChildren(...c){this.children=c;},contains(){return false;},getBoundingClientRect(){return {top:100,bottom:150,right:200,left:0,width:200,height:100};}};}
  const context={innerWidth:800,innerHeight:800,clearTimeout,setTimeout,document:{createElement:element,body:{append(){}},addEventListener(name,fn){events[name]=fn;}},window:{addEventListener(){}},Scope:{request(){throw new Error('unexpected request');}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/new_api_cockpit/static/report-currency.js'),'utf8'),context);context.window.CockpitReportCurrency.configure({usd_cny_rate:'1'});vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/new_api_cockpit/static/money-tooltip.js'),'utf8'),context);
  return {context,anchor:element(),run:code=>vm.runInContext(code,context)};
}
test('lazy hover fetches the existing snapshot exactly once without recomputation',async()=>{
  const h=tooltipHarness();let calls=0;
  h.context.Scope.request=async(path,options)=>{calls++;assert.equal(path,'/cockpit/api/statistics/usage/details');assert.deepEqual(JSON.parse(options.body),{report_id:'frozen-id',row_ids:[7]});return {ok:true,json:async()=>({rows:[{row_id:7,detail:{cost_formula:{mode:'unknown'}}}]})};};
  h.context.row={report_id:'frozen-id',row_id:7,username:'alice',model_name:'gpt',amount:'1.234567'};
  const [a,b]=await Promise.all([h.run('lazyRowMoneyFormula(row)'),h.run('lazyRowMoneyFormula(row)')]);
  assert.equal(calls,1);assert.deepEqual(Array.from(a),Array.from(b));
  assert(a.some(line=>line.includes('1.234567')));
});
test('late tooltip content cannot reappear after closing or switching the anchor',async()=>{
  const h=tooltipHarness();let resolve;
  h.context.anchor=h.anchor;h.context.pending=new Promise(r=>{resolve=r;});
  h.run('showMoneyTooltip(anchor,()=>pending)');
  assert.equal(h.run('moneyTip.hidden'),false);
  h.run('hideMoneyTooltip()');resolve(['obsolete']);await Promise.resolve();await Promise.resolve();
  assert.equal(h.run('moneyTip.hidden'),true);
  h.context.anchor2={...h.anchor,attrs:{}};let older;h.context.pending2=new Promise(r=>{older=r;});
  h.run('showMoneyTooltip(anchor,()=>pending2);showMoneyTooltip(anchor2,()=>["new content"])');
  older(['old content']);await Promise.resolve();await Promise.resolve();
  assert.equal(h.run('moneyTip.children[0].textContent'),'new content');
});

test('all tooltip pricing modes convert money and prices, never token counts or multipliers',()=>{
  const common={username:'tester',model_name:'gpt',amount:'10',request_count:1,total_tokens:100,input_tokens:100,output_tokens:0,cache_read_tokens:0,cache_write_tokens:0};
  const terms=[{label:'输入',tokens:100,price:'2'}],bucket={ratio:'2',request_count:1,terms,calculated:'10',actual:'10'};
  const formulas=[
    {mode:'unknown'},
    {mode:'historical',prices:{input_price:'2',output_price:null,cache_price:null,write_price:null},buckets:[bucket],calculated:'10',difference:'0'},
    {mode:'expression',tiers:[{name:'low',input_price:'2',output_price:null,cache_price:null,write_price:null}],buckets:[bucket],calculated:'10',difference:'0'},
    {terms,ratio:'2',calculated:'10',difference:'0'},
  ];
  for(const cost_formula of formulas){
    const row={...common,cost_formula},before=JSON.stringify(row);
    const rmb=formulaLines(row,{rate:'7.2'}),usd=formulaLines(row,{rate:'7.2',currency:'USD'});
    assert(rmb.some(line=>line.includes('实际消费金额：¥ 72')));
    assert(usd.some(line=>line.includes('实际消费金额：$ 10')));
    if(cost_formula.mode!=='unknown'){
      assert(rmb.some(line=>line.includes('100 × 14.4')));
      assert(usd.some(line=>line.includes('100 × 2')));
      assert(rmb.some(line=>line.includes('× 2 ÷ 1,000,000')));
      assert(rmb.some(line=>line.includes('RMB / 百万 Token')));
    }
    assert.equal(JSON.stringify(row),before);
  }
});
test('switching currency reuses the same lazy raw detail instead of fetching or converting twice',async()=>{
  const h=tooltipHarness();let calls=0;
  h.context.window.CockpitReportCurrency.configure({usd_cny_rate:'7.2'});
  h.context.Scope.request=async()=>{calls++;return {ok:true,json:async()=>({rows:[{detail:{cost_formula:{mode:'unknown'}}}]})};};
  h.context.row={report_id:'same-snapshot',row_id:0,username:'fixture',model_name:'model',amount:'1.25'};
  const rmb=await h.run('lazyRowMoneyFormula(row)');assert(rmb.some(line=>line.includes('¥ 9')));
  h.context.window.CockpitReportCurrency.select('USD');
  const usd=await h.run('lazyRowMoneyFormula(row)');assert(usd.some(line=>line.includes('$ 1.25')));
  assert.equal(calls,1);assert.equal(h.context.row.amount,'1.25');
});
