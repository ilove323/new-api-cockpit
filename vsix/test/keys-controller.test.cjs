'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {KeysController} = require('../dist/keys-controller');
const {KeyWriteError} = require('../dist/keys-client');
const {BalanceError} = require('../dist/client');
const {RefreshGate} = require('../dist/gate');
const CONNECTION = {endpoint: 'https://example.com/cockpit/mcp', pat: 'nap_fixture_key_manager_token_only'};
const KEY = {id: 1, name: 'key', group: 'auto', masked: 'sk-xxxx••••yyyy', status: 1, expired_time: -1, unlimited_quota: true, remain_quota: 0};
const deferred = () => {let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve};};
async function fixture(t) {
  let now = Date.now(); const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'cockpit-vsix-keys-'));
  t.after(() => fs.rm(dir, {recursive: true, force: true})); const counts = {list: 0, groups: 0, create: 0, remove: 0, changeGroup: 0, reveal: 0}, events = [];
  let rows = [{...KEY}], nextId = 1;
  const fake = {
    async list(page) {counts.list++; events.push('list'); return {items: rows.map(key => ({...key})), total: rows.length, page};},
    async groups() {counts.groups++; return [{name: '', description: 'default'}, {name: 'auto', description: 'auto'}];},
    async create(input) {counts.create++; events.push('create'); rows.push({...KEY, ...input, id: ++nextId, name: input.name || 'new'});},
    async remove(id) {counts.remove++; events.push('remove'); rows = rows.filter(key => key.id !== id);},
    async changeGroup(key, group) {counts.changeGroup++; events.push('changeGroup'); rows = rows.map(row => row.id === key.id ? {...row, group} : row);},
    async reveal() {counts.reveal++; return 'sk-fixture_revealed_secret_1234567890';}
  };
  const gate = new RefreshGate(dir, () => now), controller = new KeysController(() => {}, () => fake, () => now);
  controller.setConnection(CONNECTION); t.after(() => controller.dispose());
  return {controller, counts, fake, gate, dir, events, advance: n => now += n, now: () => now};
}
test('connection does not load keys; cached pages never expire and revisiting them never sends requests', async t => {
  const f = await fixture(t); assert.equal(f.counts.list, 0); assert.equal(f.counts.reveal, 0);
  await Promise.all([f.controller.load(), f.controller.load()]); assert.equal(f.counts.list, 1);
  await f.controller.load(); assert.equal(f.counts.list, 1);
  f.advance(7 * 86400000); await f.controller.load(); assert.equal(f.counts.list, 1);
  await f.controller.load(2); assert.equal(f.counts.list, 2);
  f.advance(7 * 86400000); await f.controller.load(1); assert.equal(f.counts.list, 2); assert.equal(f.controller.state.page.page, 1);
  assert.equal(f.counts.reveal, 0);
});
test('every mutation queries before and after in order, with no minimum interval between sequential actions', async t => {
  const f = await fixture(t); await f.controller.load(); await f.controller.load(2); await f.controller.groups();
  for (const [name, action] of [['create', () => f.controller.create({})], ['remove', () => f.controller.remove(f.controller.state.page.items.find(k => k.id === 2))], ['changeGroup', () => f.controller.changeGroup(KEY, '')]]) {
    f.events.length = 0; const before = f.counts.list, result = await action();
    assert.equal(result.applied, true); assert.equal(f.counts[name], 1); assert.equal(f.counts.list, before + 2);
    assert.deepEqual(f.events, ['list', name, 'list']);
    await f.controller.load(); assert.equal(f.counts.list, before + 2);
  }
  assert.equal(f.counts.groups, 1, 'mutations reuse group choices instead of rereading them');
  assert.equal(f.counts.reveal, 0);
});
test('double clicks cannot create twice, but a lost response never locks subsequent manual operations', async t => {
  const f = await fixture(t), started = deferred(), hold = deferred();
  f.fake.create = async () => {f.counts.create++; started.resolve(); await hold.promise; throw new KeyWriteError('uncertain', true);};
  const pending = f.controller.create({}); await started.promise;
  assert.equal((await f.controller.create({})).applied, false); hold.resolve(); await pending;
  assert.equal(f.counts.create, 1); assert.equal('uncertain' in f.controller.state, false);
  assert.equal((await f.controller.remove(KEY)).applied, true); assert.equal(f.counts.remove, 1);
});
test('confirmed mutation plus failed list refresh remains confirmed, without reissuing the write', async t => {
  const f = await fixture(t); f.fake.list = async page => {if (++f.counts.list === 2) throw new BalanceError('read failed'); return {page, total: 1, items: [KEY]};};
  const result = await f.controller.create({}); assert.equal(result.applied, true); assert.match(result.message, /列表未能更新/);
  assert.equal(f.counts.create, 1); assert.equal('uncertain' in f.controller.state, false);
});
test('a failed pre-action query prevents mutation; there is no write or automatic retry', async t => {
  const f = await fixture(t); f.fake.list = async () => {f.counts.list++; throw new BalanceError('read failed');};
  assert.equal((await f.controller.create({})).applied, false);
  assert.equal(f.counts.create, 0); assert.equal(f.counts.list, 1);
});
test('429 is reported once, without automatic retry or local cooldown blocking the next manual query', async t => {
  const f = await fixture(t), until = f.now() + 3600000;
  f.fake.list = async page => {if (++f.counts.list === 1) throw new BalanceError('429', 429, until); return {page, total: 1, items: [KEY]};};
  await f.controller.refresh(); assert.equal(f.counts.list, 1); assert.match(f.controller.state.error, /429/);
  await f.controller.refresh(); assert.equal(f.counts.list, 2); assert.equal(f.controller.state.error, undefined);
  assert.equal(await f.gate.backoff(CONNECTION.endpoint), 0, 'KEY queries do not schedule local backoff');
});
test('full keys are first read explicitly then copied from connection memory, never kept in state or files', async t => {
  const f = await fixture(t), clipboard = []; await f.controller.load();
  assert.equal(f.counts.reveal, 0); await f.controller.copy(KEY, async key => clipboard.push(key));
  await f.controller.copy(KEY, async key => clipboard.push(key));
  await f.controller.copy({...KEY, id: 2}, async () => {});
  f.advance(86400000);
  await f.controller.copy(KEY, async key => clipboard.push(key));
  assert.equal(f.counts.reveal, 2); assert.equal(clipboard.length, 3);
  assert.equal(JSON.stringify(f.controller.state).includes(clipboard[0]), false);
  const disk = (await Promise.all((await fs.readdir(f.dir)).filter(n => n.endsWith('.json')).map(n => fs.readFile(path.join(f.dir, n), 'utf8')))).join('');
  assert.equal(disk.includes(clipboard[0]), false); assert.equal(disk.includes(CONNECTION.pat), false);
});
test('late list or secret results after account change cannot enter the new view or clipboard', async t => {
  const f = await fixture(t), started = deferred(), held = deferred(); let copied = false;
  f.fake.reveal = async () => {started.resolve(); await held.promise; return 'sk-old_private_value';};
  const pending = f.controller.copy(KEY, async () => {copied = true;}); await started.promise;
  f.controller.setConnection({...CONNECTION, pat: 'nap_other_account_fixture_only'}); held.resolve(); await pending;
  assert.equal(copied, false); assert.equal(f.controller.state.page, undefined);
});
test('missing optional reveal/write permissions do not discard an authorized masked key list', async t => {
  const f = await fixture(t); await f.controller.load();
  f.fake.reveal = async () => {throw new BalanceError('missing reveal', 403, 0, 'api_key:reveal');};
  await assert.rejects(f.controller.copy(KEY, async () => {})); assert.equal(f.controller.state.page.items[0].id, KEY.id);
});
test('group picker metadata is cached without any background refresh', async t => {
  const f = await fixture(t); await Promise.all([f.controller.groups(), f.controller.groups()]); assert.equal(f.counts.groups, 1);
  f.advance(30 * 86400000); await f.controller.groups(); assert.equal(f.counts.groups, 1); assert.equal(f.counts.list, 0);
});
test('only explicit refresh replaces a cached page and resets groups and full-key memory', async t => {
  const f = await fixture(t); await f.controller.load(); await f.controller.groups(); await f.controller.copy(KEY, async () => {});
  f.fake.list = async page => {f.counts.list++; return {items: [{...KEY, name: 'updated on server'}], total: 1, page};};
  f.advance(86400000); await f.controller.load(); assert.equal(f.controller.state.page.items[0].name, 'key');
  await f.controller.refresh(); assert.equal(f.counts.list, 2); assert.equal(f.controller.state.page.items[0].name, 'updated on server');
  await f.controller.groups(); await f.controller.copy(KEY, async () => {});
  assert.equal(f.counts.groups, 2); assert.equal(f.counts.reveal, 2);
  await f.controller.refresh(); assert.equal(f.counts.list, 3, 'the next manual query has no waiting interval');
});
test('balance cooldown never blocks an explicit KEY query; cached reads remain local', async t => {
  const f = await fixture(t); await f.controller.load(); await f.controller.groups(); await f.controller.copy(KEY, async () => {});
  await f.gate.defer(CONNECTION.endpoint, f.now() + 3600000);
  await f.controller.load(); await f.controller.groups(); await f.controller.copy(KEY, async () => {});
  assert.equal(f.counts.list, 1); assert.equal(f.counts.groups, 1); assert.equal(f.counts.reveal, 1);
  await f.controller.refresh(); assert.equal(f.counts.list, 2);
});
test('deleting a key clears its full secret even if the single post-write refresh fails', async t => {
  const f = await fixture(t); await f.controller.copy(KEY, async () => {}); f.advance(60000);
  f.fake.list = async page => {if (++f.counts.list === 2) throw new BalanceError('read failed'); return {page, total: 1, items: [KEY]};};
  assert.equal((await f.controller.remove(KEY)).applied, true);
  f.fake.reveal = async () => {f.counts.reveal++; throw new BalanceError('deleted', 404);};
  await assert.rejects(f.controller.copy(KEY, async () => assert.fail('must not copy removed cached key')));
  assert.equal(f.counts.reveal, 2); assert.equal(f.counts.list, 2);
});
test('failed and uncertain mutations still query afterward without retrying the write', async t => {
  const f = await fixture(t); f.fake.create = async () => {f.counts.create++; f.events.push('create'); throw new KeyWriteError('uncertain', true);};
  await f.controller.groups(); const result = await f.controller.create({});
  assert.equal(result.applied, false); assert.deepEqual(f.events, ['list', 'create', 'list']);
  assert.equal(f.counts.create, 1); assert.equal('uncertain' in f.controller.state, false);
  await f.controller.refresh(); assert.equal('uncertain' in f.controller.state, false);
});
test('a changed name does not block operations on the same owned KEY id', async t => {
  const f = await fixture(t), list = f.fake.list;
  f.fake.list = async page => {const value = await list(page); return {...value, items: value.items.map(key => ({...key, name: 'changed'}))};};
  assert.equal((await f.controller.changeGroup(KEY, '')).applied, true); assert.equal(f.counts.changeGroup, 1);
  assert.equal((await f.controller.remove(KEY)).applied, true); assert.equal(f.counts.remove, 1);
});
test('a deleted key with a lost acknowledgement is confirmed by readback without a browser or another DELETE', async t => {
  const f = await fixture(t), remove = f.fake.remove;
  f.fake.remove = async id => {await remove(id); throw new KeyWriteError('lost acknowledgement', true);};
  const result = await f.controller.remove(KEY);
  assert.equal(result.applied, true); assert.equal(f.counts.remove, 1); assert.deepEqual(f.events, ['list', 'remove', 'list']);
  assert.equal(f.controller.state.error, undefined); assert.equal(f.controller.state.page.total, 0);
  assert.equal((await f.controller.create({})).applied, true);
});
test('a group change with a lost acknowledgement is confirmed by the updated group', async t => {
  const f = await fixture(t), change = f.fake.changeGroup;
  f.fake.changeGroup = async (key, group) => {await change(key, group); throw new KeyWriteError('lost acknowledgement', true);};
  assert.equal((await f.controller.changeGroup(KEY, '')).applied, true);
  assert.equal(f.counts.changeGroup, 1); assert.equal(f.controller.state.page.items[0].group, '');
  assert.equal(f.controller.state.error, undefined);
});
test('a second-page group change with a lost acknowledgement is read back without repeating the write', async t => {
  const f = await fixture(t), pages = []; let applied = false;
  f.fake.list = async page => {
    pages.push(page);
    const rows = Array.from({length: 100}, (_, i) => ({...KEY, id: 100 - i, group: applied && i === 99 ? '' : 'auto'}));
    return {page, total: rows.length, items: rows.slice((page - 1) * 50, page * 50)};
  };
  f.fake.changeGroup = async () => {f.counts.changeGroup++; applied = true; throw new KeyWriteError('lost acknowledgement', true);};
  await f.controller.load(2);
  assert.equal((await f.controller.changeGroup(KEY, '')).applied, true);
  assert.equal(f.counts.changeGroup, 1); assert.deepEqual(pages, [2, 2, 1, 2]);
  assert.equal(f.controller.state.page.page, 1); assert.equal(f.controller.state.error, undefined);
});
test('deletion outside the first page requires a complete consistent readback', async t => {
  const f = await fixture(t); let applied = false;
  f.fake.list = async page => {
    const rows = Array.from({length: 100}, (_, i) => ({...KEY, id: 100 - i})).filter(key => !applied || key.id !== KEY.id);
    return {page, total: rows.length, items: rows.slice((page - 1) * 50, page * 50)};
  };
  f.fake.remove = async () => {f.counts.remove++; applied = true; throw new KeyWriteError('lost acknowledgement', true);};
  await f.controller.load(2);
  assert.equal((await f.controller.remove(KEY)).applied, true); assert.equal(f.counts.remove, 1);
});
test('new key with a lost acknowledgement is confirmed by its unique addition, including from page two', async t => {
  const f = await fixture(t), create = f.fake.create;
  f.fake.create = async input => {await create(input); throw new KeyWriteError('lost acknowledgement', true);};
  await f.controller.load(2);
  assert.equal((await f.controller.create({name: 'new unique', group: 'auto', expired_time: -1, unlimited_quota: true, remain_quota: 0})).applied, true);
  assert.equal(f.counts.create, 1); assert.equal(f.controller.state.page.page, 1); assert.equal(f.controller.state.error, undefined);
});
test('concurrent matching additions never guess which create succeeded or resend it', async t => {
  const f = await fixture(t), create = f.fake.create;
  f.fake.create = async input => {await create(input); await create(input); throw new KeyWriteError('lost acknowledgement', true);};
  const result = await f.controller.create({name: 'same name', group: 'auto', expired_time: -1, unlimited_quota: true, remain_quota: 0});
  assert.equal(result.applied, false); assert.equal(f.counts.create, 2, 'two simulated concurrent additions, no third request');
  assert.equal(f.controller.state.busy, false);
});
test('an unchanged server record is not reported as a successful delete', async t => {
  const f = await fixture(t); f.fake.remove = async () => {f.counts.remove++;};
  assert.equal((await f.controller.remove(KEY)).applied, false);
  assert.match(f.controller.state.error, /未显示修改生效/); assert.equal(f.counts.remove, 1);
});
test('a connection change during the pre-action query cannot modify either account', async t => {
  const f = await fixture(t), started = deferred(), held = deferred();
  f.fake.list = async page => {f.counts.list++; started.resolve(); await held.promise; return {page, total: 1, items: [KEY]};};
  const pending = f.controller.remove(KEY); await started.promise;
  f.controller.setConnection({...CONNECTION, pat: 'nap_other_account_fixture_only'}); held.resolve();
  assert.equal((await pending).applied, false); assert.equal(f.counts.remove, 0); assert.equal(f.counts.list, 1);
});
