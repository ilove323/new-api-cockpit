/* DOM fixtures only: table formatting must not round any underlying amounts. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'../src/new_api_statistics/static');
class Element{
  constructor(){this.children=[];this.dataset={};this.style={setProperty(){}};this.classList={toggle(){},add(){}};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(){}
  setAttribute(){}
}
function harness(){
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const context={URLSearchParams,
    window:{location:{search:'?dev=2'},addEventListener(){}},
    document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>[],querySelector:get},
    hideMoneyTooltip(){},bindMoneyTooltip:(cell,formula)=>{cell.formula=formula;},
    rowMoneyFormula:row=>row.amount,totalMoneyFormula:(rows,amount)=>amount,
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root,'app.js'),'utf8'),context);
  return {elements,run:code=>vm.runInContext(code,context)};
}
const row={user_id:1,username:'tester',model_name:'gpt',tier_name:'low',token_id:1,token_name:'key',
  request_count:2,total_tokens:1000000,input_tokens:800000,output_tokens:10,
  cache_read_tokens:199990,cache_write_tokens:0,group_ratio:3.456789,
  input_price:2,output_price:10.123456,cache_price:0.0049,write_price:null,amount:1234.567891};
test('table currency formatter fixes two decimals, including zeros, rounding and missing values',()=>{
  const h=harness();
  for(const [value,expected] of [[2,'2.00'],[0,'0.00'],[0.0049,'0.00'],[0.005,'0.01'],[-12.3456,'-12.35'],['1234.567891','1,234.57'],[null,'—']]){
    assert.equal(h.run(`tableMoney(${JSON.stringify(value)})`),expected);
  }
  assert.equal(h.run('tableMoney(undefined)'),'—');
  assert.equal(h.run('number(3.456789)'),'3.456789');
  assert.equal(h.run("priceDisplay({pricing_mode:'expression'},'input_price')"),'无法拆分');
});
test('usage rows, monetary prices, developer unit prices and footer use two decimals only',()=>{
  const h=harness();h.run(`snapshot={rows:[${JSON.stringify(row)}]};renderDetails()`);
  const cells=new Map(h.elements.get('rows').children[0].children.map(c=>[c.dataset.column,c]));
  for(const [column,expected] of Object.entries({input_price:'2.00',output_price:'10.12',cache_price:'0.00',write_price:'—',amount:'1,234.57',amount_per_million:'1,234.57',group_ratio:'3.456789',total_tokens:'1,000,000'})){
    assert.equal(cells.get(column).textContent,expected,column);
  }
  const footer=h.elements.get('totals').children[0].children.find(c=>c.dataset.column==='amount');
  assert.equal(footer.textContent,'1,234.57');
  assert.equal(cells.get('amount').formula(),row.amount);
  assert.equal(footer.formula(),row.amount);
  assert.equal(h.run('snapshot.rows[0].amount'),row.amount);
});
test('token detail and model-summary tables keep the same presentation-only formatting',()=>{
  for(const [detail,model] of [['token','model'],['summary','summary'],['token','summary']]){
    const h=harness();h.run(`detailMode='${detail}';modelMode='${model}';snapshot={rows:[${JSON.stringify(row)}]};tokenSnapshot=snapshot;renderDetails()`);
    const amount=h.elements.get('rows').children[0].children.find(c=>c.dataset.column==='amount');
    assert.equal(amount.textContent,'1,234.57');assert.equal(amount.formula(),row.amount);
  }
});
