const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function formulaLines(row) {
  const context = {
    document: {
      createElement: () => ({addEventListener() {}}),
      body: {append() {}},
      addEventListener() {},
    },
    window: {addEventListener() {}},
  };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../src/new_api_statistics/static/money-tooltip.js'), 'utf8'),
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
      mode: 'historical', matched_tier: true, converted: true,
      prices: {input_price: '2', output_price: '10', cache_price: '0.2', write_price: null},
      buckets: [
        {ratio: '3.4', current_ratio: true, request_count: 2, actual: '0.00032',
         calculated: '0.00032028', terms: [{tokens: 20, price: '2'}]},
        {ratio: '3', current_ratio: false, request_count: 1, actual: '0.00016',
         calculated: '0.00016', terms: [{tokens: 10, price: '2'}]},
      ],
      calculated: '0.00048028', difference: '-0.00000028',
    },
  });
  assert.equal(rows.filter(line => line.startsWith('档位：')).length, 1);
  assert.equal(rows.filter(line => line.startsWith('当前倍率 ')).length, 1);
  assert.equal(rows.filter(line => line.startsWith('历史倍率 ')).length, 1);
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
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/new_api_statistics/static/money-tooltip.js'),'utf8'),context);
  return {context,anchor:element(),run:code=>vm.runInContext(code,context)};
}
test('lazy hover fetches the existing snapshot exactly once without recomputation',async()=>{
  const h=tooltipHarness();let calls=0;
  h.context.Scope.request=async(path,options)=>{calls++;assert.equal(path,'/cockpit/statistics/api/usage/details');assert.deepEqual(JSON.parse(options.body),{report_id:'frozen-id',row_ids:[7]});return {ok:true,json:async()=>({rows:[{row_id:7,detail:{cost_formula:{mode:'unknown'}}}]})};};
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
