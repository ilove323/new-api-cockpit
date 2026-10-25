'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {AccountController} = require('../dist/controller');
const {BalanceError} = require('../dist/client');
const {RefreshGate} = require('../dist/gate');
const CONNECTION = {endpoint: 'https://example.com/cockpit/mcp', pat: 'nap_fixture_not_a_real_credential'};
const BALANCE = {user: {id: 1, username: 'fixture'}, remaining_amount: '10', used_amount: '2', request_count: 3, quota_per_unit: 500000, amount_unit: 'fixture'};
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
async function fixture(t, query) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'cockpit-vsix-gate-'));
  t.after(() => fs.rm(dir, {recursive: true, force: true}));
  let now = Date.now(); const gate = new RefreshGate(dir, () => now);
  const controller = new AccountController(gate, () => {}, query, () => now);
  controller.setConnection(CONNECTION); t.after(() => controller.dispose());
  return {dir, gate, controller, now: () => now, advance: ms => now += ms};
}
test('parallel refreshes share one query; manual refresh has a durable 60-second cooldown', async t => {
  let calls = 0; const hold = deferred();
  const f = await fixture(t, async () => { calls++; await hold.promise; return BALANCE; });
  const first = f.controller.refresh(), second = f.controller.refresh(); assert.equal(first, second);
  hold.resolve(); await first; assert.equal(calls, 1); assert.equal(f.controller.state.balance, BALANCE);
  await f.controller.refresh(); assert.equal(calls, 1);
  f.advance(59999); await f.controller.refresh(); assert.equal(calls, 1);
  f.advance(1); await f.controller.refresh(); assert.equal(calls, 2);
  const disk = (await Promise.all((await fs.readdir(f.dir)).map(name => fs.readFile(path.join(f.dir, name), 'utf8')))).join('');
  assert.equal(disk.includes(CONNECTION.pat), false); assert.equal(disk.includes('fixture'), false);
});
test('other windows and a restarted controller cannot bypass the same endpoint cooldown', async t => {
  let calls = 0; const f = await fixture(t, async () => { calls++; return BALANCE; });
  const other = new AccountController(new RefreshGate(f.dir, f.now), () => {}, async () => { calls++; return BALANCE; }, f.now);
  t.after(() => other.dispose()); other.setConnection(CONNECTION);
  await Promise.all([f.controller.refresh(), other.refresh()]); assert.equal(calls, 1);
  f.controller.setConnection(); f.controller.setConnection(CONNECTION); await f.controller.refresh(); assert.equal(calls, 1);
  assert.equal((await fs.readdir(f.dir)).some(name => name.endsWith('.lock')), false);
});
test('old MCP and new standalone site addresses share the existing refresh and 429 gate', async t => {
  const f = await fixture(t, async () => BALANCE);
  await f.gate.reserve(CONNECTION.endpoint);
  assert.equal((await f.gate.reserve('https://example.com')).allowed, false);
  await f.gate.defer(CONNECTION.endpoint, f.now() + 3600000);
  assert.equal(await f.gate.backoff('https://example.com'), f.now() + 3600000);
});
test('unit metadata is reused for an hour and cleared on account changes without caching the balance', async t => {
  const units = [];
  const f = await fixture(t, async (_connection, _signal, _timeout, unit) => {units.push(unit); return BALANCE;});
  await f.controller.refresh(); f.advance(60000); await f.controller.refresh();
  assert.deepEqual(units, [undefined, 500000]);
  f.advance(60 * 60000); await f.controller.refresh();
  assert.deepEqual(units, [undefined, 500000, undefined]);
  f.controller.setConnection({...CONNECTION, pat: 'nap_different_fixture_credential'});
  f.advance(60000); await f.controller.refresh();
  assert.deepEqual(units, [undefined, 500000, undefined, undefined]);
});
test('429 pauses at least 15 minutes, honors a longer Retry-After and survives restart', async t => {
  let calls = 0; let until;
  const f = await fixture(t, async () => { calls++; throw new BalanceError('请求过于频繁', 429, until); }); until = f.now() + 3600000;
  await f.controller.refresh(); assert.equal(calls, 1); assert.equal(f.controller.state.nextAllowedAt, until);
  f.controller.setConnection(CONNECTION); f.advance(16 * 60000); await f.controller.refresh(); assert.equal(calls, 1);
  f.advance(44 * 60000); await f.controller.refresh(); assert.equal(calls, 2);
});
test('an account change cancels an old request and its late result cannot replace the new account', async t => {
  const started = deferred(), held = deferred(); let signal;
  const f = await fixture(t, async (_connection, abort) => { signal = abort; started.resolve(); return held.promise; });
  const pending = f.controller.refresh(); await started.promise;
  f.controller.setConnection({...CONNECTION, pat: 'nap_different_fixture_credential'});
  assert.equal(signal.aborted, true); held.resolve(BALANCE); await pending;
  assert.equal(f.controller.state.balance, undefined); assert.equal(f.controller.state.loading, false);
});
test('401/403 clear old balances and stop automatic refresh eligibility, not display zero', async t => {
  let failure = false; const f = await fixture(t, async () => { if (failure) throw new BalanceError('权限失效', 401); return BALANCE; });
  await f.controller.refresh(); failure = true; f.advance(60000); await f.controller.refresh();
  assert.equal(f.controller.state.balance, undefined); assert.equal(f.controller.state.updatedAt, undefined); assert.equal(f.controller.state.authenticationFailed, true);
});
test('an old account 429 still defers the endpoint without leaking its state after an account switch', async t => {
  const started = deferred(), held = deferred(); let until;
  const f = await fixture(t, async () => { started.resolve(); await held.promise; throw new BalanceError('old private response', 429, until); });
  until = f.now() + 3600000;
  const pending = f.controller.refresh(); await started.promise;
  f.controller.setConnection({...CONNECTION, pat: 'nap_other_fixture_credential'});
  held.resolve(); await pending;
  assert.equal(f.controller.state.error, undefined); assert.equal(f.controller.state.balance, undefined);
  f.advance(60000); assert.deepEqual(await f.gate.reserve(CONNECTION.endpoint), {allowed: false, nextAllowedAt: until});
});
test('failed refresh labels the retained amount stale; a successful later read clears the error', async t => {
  let failure = false; const f = await fixture(t, async () => { if (failure) throw new BalanceError('网络不可用'); return BALANCE; });
  await f.controller.refresh(); failure = true; f.advance(60000); await f.controller.refresh();
  assert.equal(f.controller.state.balance, BALANCE); assert.equal(f.controller.state.error, '网络不可用');
  failure = false; f.advance(60000); await f.controller.refresh(); assert.equal(f.controller.state.error, undefined);
});
test('unreadable gate state fails closed instead of sending an unthrottled query', async t => {
  let calls = 0; const f = await fixture(t, async () => { calls++; return BALANCE; });
  await f.gate.reserve(CONNECTION.endpoint); const file = (await fs.readdir(f.dir)).find(name => name.endsWith('.json'));
  await fs.writeFile(path.join(f.dir, file), 'broken'); f.advance(60000); await f.controller.refresh();
  assert.equal(calls, 0); assert.match(f.controller.state.error, /刷新记录/);
});
