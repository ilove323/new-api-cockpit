'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {statusBarLabel} = require('../dist/presentation');
const balance = {user: {id: 1, username: 'demo-user'}, remaining_amount: '1234.56', used_amount: '7', request_count: 1, quota_per_unit: 500000, amount_unit: 'fixture'};
const state = changes => ({loading: false, nextAllowedAt: 0, balance, ...changes});

test('bottom status bar includes the account and available balance together', () => {
  assert.deepEqual(statusBarLabel(state()), {text: 'demo-user · 可用余额 1,234.56', accessibleLabel: 'demo-user · 可用余额 1,234.56'});
});
test('small, zero and negative available balances retain their exact amount', () => {
  for (const [value, expected] of [['0.000002', '0.000002'], ['0', '0.00'], ['-12.5', '-12.50']]) {
    assert.equal(statusBarLabel(state({balance: {...balance, remaining_amount: value}})).text, 'demo-user · 可用余额 ' + expected);
  }
});
test('a failed refresh retains the account but explicitly labels the old balance', () => {
  assert.equal(statusBarLabel(state({error: 'network unavailable'})).text, 'demo-user · 上次余额 1,234.56');
});
test('no account or a cleared authentication result cannot be presented as zero balance', () => {
  assert.equal(statusBarLabel(state({balance: undefined})).text, 'Cockpit 余额');
  assert.equal(statusBarLabel(state({balance: undefined, authenticationFailed: true, error: '401'})).text, 'Cockpit 余额');
});
test('an account name cannot introduce a status bar icon and accessibility keeps the literal name', () => {
  const result = statusBarLabel(state({balance: {...balance, user: {id: 1, username: 'demo$(warning)'}}}));
  assert.equal(result.text, 'demo\\$(warning) · 可用余额 1,234.56');
  assert.equal(result.accessibleLabel, 'demo$(warning) · 可用余额 1,234.56');
});
