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
